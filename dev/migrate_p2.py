#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 0 · P2 一次性迁移：把 Analyzer 主库的历史并进本地 Finder 库（见 方案.md §4 阶段 0）。

为什么必须做：B站接口只保留近三个月，Analyzer 里 2020–2025 的长尾（363 条）不迁就永久丢。

设计要点（对应 .trae/documents/阶段0-1.5-2-实施计划.md「提交 1」）：
- **默认 --dry-run，必须显式 --apply 才写盘**；写盘全程单事务，异常即回滚。
- 复用 `collector.init_db()` 补 `remark` / `remark_time` 两列（幂等 DDL），
  复用 `store._rec_key()` 算合并键，复用 `collector.upsert()` 落库（自带 progress 粘性保护）。
- **不用 Analyzer 的 `kid` 列**（2026 表 3659/3685 行为 0，直接拿来会撞主键）→ 统一用 (bvid,view_at) 复合键。
- **按复合键匹配本地已有行**：本地库 `kid` 存的是 B站原始 kid（形如 '116963149878437'），
  与复合键无关 —— 所以**不能指望 `ON CONFLICT(kid)` 去重**。命中已有行时沿用其原 kid 做 UPDATE，
  未命中才 INSERT 新行；否则会凭空多出约 2400 条重复行。
- 只读打开 Analyzer（`mode=ro`）；不 import server、不联网。

用法：
    python dev/migrate_p2.py            # 预演：只读、只打印将要发生的事
    python dev/migrate_p2.py --apply    # 真正写盘
"""
import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

import collector  # noqa: E402
import store  # noqa: E402

# 除 id 外全取 + remark_time（store.ANALYZER_COLS 未含后者，但迁移要带上）
COLS = [c for c in store.ANALYZER_COLS if c != "id"] + ["remark_time"]


def read_analyzer_rows(db_path):
    """只读遍历 Analyzer 全部年表 → [(computed_key, row_dict)]，保留原始行序。

    与 `store._read_analyzer()` 同款安全选列（按 pragma 过滤缺失列），但不过早折叠为 dict —— 
    迁移需要统计原始行数，并把 `remark_time` 一并带出。
    """
    if not os.path.exists(db_path):
        raise SystemExit("Analyzer 库不存在：%s" % db_path)
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.text_factory = str
    try:
        cur = con.cursor()
        rows = []
        for tb in store._list_base_year_tables(cur):
            cur.execute("SELECT name FROM pragma_table_info('%s')" % tb)
            have = {r[0] for r in cur.fetchall()}
            sel = [c for c in COLS if c in have]
            for raw in cur.execute("SELECT %s FROM %s" % (", ".join(sel), tb)):
                d = dict(zip(sel, raw))
                key = store._rec_key(d)
                rows.append((key, d))
        return rows
    finally:
        con.close()


def read_local_kid_map(db_path):
    """只读取本地库的 `复合键 -> 已存 kid` 映射（缺列的旧库也不会报错）。"""
    if not os.path.exists(db_path):
        return {}, False
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM pragma_table_info('history')")
        have = {r[0] for r in cur.fetchall()}
        has_remark = "remark" in have
        sel = [c for c in ("kid", "bvid", "oid", "view_at") if c in have]
        m = {}
        for raw in cur.execute("SELECT %s FROM history" % ", ".join(sel)):
            d = dict(zip(sel, raw))
            key = store._rec_key(d)
            if key not in m:                     # 同键多行时保留第一行（复合键已足够去重）
                m[key] = d.get("kid")
        return m, has_remark
    finally:
        con.close()


def to_fields(key, d):
    """Analyzer 行 → `collector.upsert()` 的入参（字段名对齐 history 表）。"""
    am = d.get("author_mid")
    raw = {
        "history": {"dt": d.get("dt")},
        "live_status": d.get("live_status"),
        "main_category": d.get("main_category"),
    }
    return {
        "kid": key,
        "title": d.get("title"),
        "show_title": d.get("show_title"),
        "long_title": d.get("long_title"),
        "author_name": d.get("author_name"),
        "author_mid": None if am is None else str(am),
        "view_at": d.get("view_at"),
        "bvid": d.get("bvid"),
        "oid": d.get("oid"),
        "epid": d.get("epid"),
        "cid": d.get("cid"),
        "business": d.get("business"),
        "cover": d.get("cover"),
        "progress": d.get("progress"),
        "duration": d.get("duration"),
        "uri": d.get("uri"),
        "tag_name": d.get("tag_name"),
        "badge": d.get("badge"),
        "videos": d.get("videos"),
        # Analyzer 不存原始 JSON；只回填读路径真正会解析的两个派生字段（store._read_local）
        "raw_json": json.dumps(raw, ensure_ascii=False),
        # 源为空则传 None —— upsert 用 COALESCE(excluded, history)，不会冲掉已有备注
        "remark": d.get("remark") or None,
        "remark_time": d.get("remark_time") or None,
    }


def main():
    ap = argparse.ArgumentParser(description="阶段 0 · Analyzer → 本地库 一次性迁移")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只预演）")
    ap.add_argument("--dry-run", action="store_true", help="显式预演（默认行为，仅为调用方便）")
    ap.add_argument("--analyzer-db", default=store.ANALYZER_DB)
    ap.add_argument("--local-db", default=store.LOCAL_DB)
    args = ap.parse_args()

    print("Analyzer 库：%s" % args.analyzer_db)
    print("本地库　　：%s" % args.local_db)
    print("模式　　　：%s" % ("APPLY（写盘）" if args.apply else "DRY-RUN（只读预演）"))
    print("-" * 68)

    rows = read_analyzer_rows(args.analyzer_db)
    local_map, has_remark = read_local_kid_map(args.local_db)
    print("Analyzer 原始行　　：%d" % len(rows))
    print("本地库已有记录键　：%d（remark 列：%s）" % (len(local_map), "有" if has_remark else "无"))

    seen = set()
    plan = []          # [(action, stored_kid 或 None, computed_key, fields)]
    dup_in_src = 0
    bad_key = 0
    for key, d in rows:
        if not (d.get("bvid") or d.get("oid") or d.get("kid")):
            bad_key += 1                  # 三个标识全空 → 算不出合并键，跳过
            continue
        if key in seen:
            dup_in_src += 1
            continue
        seen.add(key)
        if key in local_map:
            plan.append(("update", local_map[key], key, to_fields(key, d)))
        else:
            plan.append(("insert", None, key, to_fields(key, d)))

    n_ins = sum(1 for a, _, _, _ in plan if a == "insert")
    n_upd = len(plan) - n_ins
    n_remark = sum(1 for _, _, _, f in plan if f["remark"])
    print("库内重复 / 无键跳过：%d / %d" % (dup_in_src, bad_key))
    print("将新增（本地没有）　：%d" % n_ins)
    print("将更新（命中已有）　：%d" % n_upd)
    print("其中 remark 非空　　：%d" % n_remark)
    print("迁移后本地预期行数　：%d" % (len(local_map) + n_ins))
    print("-" * 68)

    if not args.apply:
        print("DRY-RUN 结束，未写任何数据。确认无误后加 --apply 重跑。")
        return

    now = int(time.time())
    conn = collector.init_db(args.local_db)     # 幂等补 remark / remark_time 两列
    try:
        with conn:                              # 单事务：异常自动回滚
            for _action, stored_kid, key, fields in plan:
                # 命中已有行 → 沿用其原 kid，走 upsert 的 ON CONFLICT 分支（progress 粘性保护同款生效）
                fields = dict(fields, kid=stored_kid or key)
                collector.upsert(conn, fields, now)
    except Exception:
        conn.close()
        raise
    conn.close()
    print("APPLY 完成：新增 %d 条、更新 %d 条。" % (n_ins, n_upd))

    after, has_remark_after = read_local_kid_map(args.local_db)
    print("复核：本地库记录键 %d、remark 列 %s" % (len(after), "有" if has_remark_after else "无"))
    con = sqlite3.connect("file:%s?mode=ro" % args.local_db, uri=True)
    try:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM history")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM history WHERE remark IS NOT NULL AND remark <> ''")
        rem = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM history WHERE view_at < 1767225600")  # 2026-01-01 之前
        old = cur.fetchone()[0]
    finally:
        con.close()
    print("复核：history 共 %d 行（2026 年前 %d 行）、remark 非空 %d 行" % (total, old, rem))


if __name__ == "__main__":
    main()