#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读适配器（验证用）：把 BilibiliHistoryAnalyzer 的全量 SQLite 接进续看规则引擎。

目标：验证「方案 B / Phase 1 选项① 直连 SQLite」可行性 —— 以 file:?mode=ro 打开 Analyzer 库，
动态枚举 bilibili_history_YYYY 基表（排除 _fts 全文虚拟表），跨年 UNION 读取，映射为 canonical
derived 后跑通 needs/skipped/stale 三视图分类。

原则：
- 全程只读（mode=ro），绝不 INSERT/UPDATE/DELETE 源库；
- 仅消费 Analyzer 34 列（canonical derived 基准），其缺的 archived_only 统一补 0；
- 输出验证报告（各视图计数 + 命中样本），供 doc/adapter-验证报告.md 引用。
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import engine  # noqa: E402

# Analyzer 库（主源，read-only 直连）
ANALYZER_DB = r"D:\Program Files (x86)\BilibiliHistoryAnalyzer\output\bilibili_history.db"
# 规则定义沿用 Finder 当前 data/rules.json（active 规则即分类依据）
RULES_FILE = os.path.join(PROJECT_ROOT, "data", "rules.json")

# 从 Analyzer 基表选取的 canonical 字段（34 列中的规则相关子集 + 展示用）
SELECT_COLS = [
    "id", "kid", "bvid", "title", "business", "author_name", "author_mid",
    "view_at", "progress", "duration", "cover", "uri", "oid", "dt", "tag_name",
    "live_status", "main_category",
]


def list_base_year_tables(cur):
    """枚举 bilibili_history_YYYY 基表，排除 _fts 全文虚拟表。"""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name GLOB 'bilibili_history_[0-9][0-9][0-9][0-9]' ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def read_analyzer_records(db_path):
    """read-only 跨年 UNION 读取全量记录，映射为 canonical derived。"""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Analyzer 库不存在: {db_path}")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.text_factory = str
    try:
        cur = con.cursor()
        tables = list_base_year_tables(cur)
        if not tables:
            raise RuntimeError("未找到任何 bilibili_history_YYYY 基表")
        cols_sql = ", ".join(SELECT_COLS)
        records = []
        per_table = {}
        for tb in tables:
            rows = cur.execute(
                f"SELECT {cols_sql} FROM {tb}"
            ).fetchall()
            per_table[tb] = len(rows)
            for row in rows:
                d = dict(zip(SELECT_COLS, row))
                d["archived_only"] = 0  # Analyzer 无此列，按 0 补足（同步簿记属 Finder 侧）
                records.append(d)
        return records, tables, per_table
    finally:
        con.close()


def main():
    print("=" * 60)
    print("Analyzer 只读适配器 · 续看三视图验证")
    print("=" * 60)
    rules = engine.load_rules_from_file(RULES_FILE)
    rule = engine.get_active_rule(rules)
    if not rule:
        print("✗ 没有 active 规则")
        return 1
    print(f"active 规则: id={rule.get('id')} name={rule.get('name')} "
          f"分组数={len(rule.get('groups', []))}")
    # 冲突校验
    v = engine.validate_rule(rule)
    if v["hard"]:
        print("⚠ 规则硬冲突：", v["hard"])
    if v["soft"]:
        print("· 规则软警告：", v["soft"])

    records, tables, per_table = read_analyzer_records(ANALYZER_DB)
    print(f"\n已 read-only 读取 Analyzer 全量记录：{len(records)} 条（跨 {len(tables)} 张年表）")
    for tb in tables:
        print(f"  - {tb}: {per_table[tb]}")

    result = engine.classify(records, rule)
    print("\n---- 三视图分类结果（只读，未写库）----")
    print(f"  总记录数 total      : {result['total']}")
    print(f"  已看完 finished      : {result['finished']}")
    print(f"  自动跳过 auto_skip   : {result['auto_skip']}")
    print(f"  已搁置 stale         : {result['stale']}")
    print(f"  需要观看 needs       : {result['needs']}")
    print(f"  (校验: finished+auto_skip+stale+needs = "
          f"{result['finished']+result['auto_skip']+result['stale']+result['needs']} "
          f"应等于 total)")

    print("\n---- 命中样本 ----")
    for cat in ("auto_skip", "stale", "needs"):
        print(f"[{cat}]")
        for s in result["samples"][cat]:
            prog = s.get("progress")
            dur = s.get("duration")
            pct = f"{prog/dur*100:.1f}%" if (prog and dur and dur > 0 and prog != -1) else ("100%" if prog == -1 else "未知")
            print(f"    {s.get('title','')[:30]!r} | {s.get('business')} | progress={pct} "
                  f"| view_at={s.get('view_at')} | "
                  f"{('action='+s['action']+' label='+s['label']) if 'action' in s else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
