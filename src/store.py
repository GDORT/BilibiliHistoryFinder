#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 数据层（可移植）：把多源历史接进续看规则引擎。

设计要点（对应正式版方案 §3.3 / §4 / §6.2）：
- **主源 = Analyzer SQLite（read-only 直连）**：34 列、2020–2026 深历史，跨年表 UNION，排除 _fts 虚拟表。
- **备份源 = 本地 Finder 库（read-only）**：collector.py 拉取的近期窗口；与主源按 kid 合并（主源优先，本地补齐主源没有的）。
- **分类层（auto_skip / stale / manual_skip）绝不回写源库**：仅我们的规则引擎私有状态落 side DB
  （data/canonical_state.db），与源库解耦、互不污染。
- 分类由 engine 确定性计算（规则命中即 auto_skip），manual_skip / auto_exempt（用户取消自动跳过）持久化在 side DB。
- 全程不 import collector、不直接写 Analyzer / Finder 源库；符合「源库只读」原则。
"""
import hashlib
import json
import os
import sqlite3
import time

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# Analyzer 库路径（可用环境变量 ANALYZER_DB 覆盖，便于迁移）
ANALYZER_DB = os.environ.get(
    "ANALYZER_DB",
    r"D:\Program Files (x86)\BilibiliHistoryAnalyzer\output\bilibili_history.db",
)
# 本地 Finder 库（备份源，collector.py 写入）
LOCAL_DB = os.path.join(DATA_DIR, "bilibili_history.db")
# 侧状态库（规则引擎私有：manual_skip / auto_exempt / 应用哈希）
STATE_DB = os.path.join(DATA_DIR, "canonical_state.db")

# 从 Analyzer 基表选取的 canonical 字段（34 列中的规则相关子集 + 展示用）
# 扩展：加入 remark / is_fav / videos，供高级筛选器使用（按表安全选列见 _read_analyzer）。
ANALYZER_COLS = [
    "id", "kid", "bvid", "title", "business", "author_name", "author_mid",
    "view_at", "progress", "duration", "cover", "uri", "oid", "dt", "tag_name",
    "live_status", "main_category", "remark", "is_fav", "videos",
]


def _list_base_year_tables(cur):
    """枚举 bilibili_history_YYYY 基表，排除 _fts 全文虚拟表。"""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name GLOB 'bilibili_history_[0-9][0-9][0-9][0-9]' ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def _rec_key(d):
    """统一合并键 / skip 标识：优先 (bvid,view_at)，回退 (oid,view_at)，再回退 (kid,view_at)。

    实测：Analyzer 与 Finder 对同一视频的 `oid` 不一致，但 `bvid` 稳定一致，且 (bvid,view_at)
    重叠 2389 条；而 `kid` 列 Analyzer 恒为 0、Finder 为 oid（无 view_at）—— 故必须以 (bvid,view_at)
    为合并键才能正确去重。该键同时覆盖输出 kid 字段，保证 skip 持久化跨源一致。
    """
    bvid = d.get("bvid")
    oid = d.get("oid")
    kid = d.get("kid")
    va = d.get("view_at")
    if bvid:
        return f"{bvid}_{va}"
    if oid:
        return f"{oid}_{va}"
    return f"{kid}_{va}"


def _read_analyzer(db_path):
    """read-only 跨年 UNION 读取 Analyzer 全量记录 → {kid: canonical dict}。

    按表安全选列：每张年表只 SELECT 其实际存在的列（remark/is_fav/videos 等
    可能仅新版本 Analyzer 才有），避免 PRAGMA 缺失列导致整表读取失败。
    """
    if not os.path.exists(db_path):
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.text_factory = str
    try:
        cur = con.cursor()
        tables = _list_base_year_tables(cur)
        recs = {}
        for tb in tables:
            cur.execute(f"SELECT name FROM pragma_table_info('{tb}')")
            have = {r[0] for r in cur.fetchall()}
            sel = [c for c in ANALYZER_COLS if c in have]
            for row in cur.execute(f"SELECT {', '.join(sel)} FROM {tb}"):
                d = dict(zip(sel, row))
                d.setdefault("remark", "")
                d.setdefault("is_fav", 0)
                d.setdefault("videos", 1)
                d["archived_only"] = 0  # Analyzer 无此列，按 0 补足
                d["source"] = "analyzer"
                d["kid"] = _rec_key(d)  # 覆盖无效 kid 列，统一复合键
                recs[d["kid"]] = d
        return recs
    finally:
        con.close()


def _read_local(db_path):
    """read-only 读取本地 Finder 库（备份源）→ {kid: canonical dict}。"""
    if not os.path.exists(db_path):
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.text_factory = str
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM pragma_table_info('history')")
        have = {r[0] for r in cur.fetchall()}
        base = ["kid", "title", "author_name", "author_mid", "view_at", "bvid",
                "business", "cover", "progress", "duration", "uri", "archived_only",
                "raw_json"]
        sel = [c for c in base if c in have]
        recs = {}
        for row in cur.execute(f"SELECT {', '.join(sel)} FROM history"):
            d = dict(zip(sel, row))
            raw = d.pop("raw_json", None)
            if raw:
                try:
                    rj = json.loads(raw)
                    d["dt"] = (rj.get("history") or {}).get("dt")
                    d["live_status"] = rj.get("live_status")
                except Exception:
                    pass
            d.setdefault("archived_only", 0)
            d["source"] = "local"
            d["kid"] = _rec_key(d)  # 统一复合键（与 Analyzer 同格式）
            recs[d["kid"]] = d
        return recs
    finally:
        con.close()


def _fold_latest_by_bvid(records):
    """Plan A：同一 bvid 只保留 view_at 最大的一条，对齐 B站官网历史页行为。

    背景：B站历史接口每次「观看会话」记一条独立记录（各自 view_at），同一视频
    当天多次打开 → 多条。Analyzer 与开源 Frontend 均保留全部会话、展示层不折叠；
    本函数让 Finder 网页与「B站官网历史页」一致（每视频只显示最近一次观看）。

    规则：
    - 无 bvid 的记录（直播/专栏等）不参与折叠，原样保留；
    - 折叠后**保留原始记录 dict（含其 kid=(bvid,view_at)）**，不重算键，
      故既有 manual_skip / auto_exempt 等跳过状态对「最新一条」继续有效，
      canonical_state.db 无需迁移。
    """
    best = {}
    rest = []
    for r in records:
        bvid = r.get("bvid")
        if not bvid:
            rest.append(r)
            continue
        va = int(r.get("view_at") or 0)
        cur = best.get(bvid)
        if cur is None or va > int(cur.get("view_at") or 0):
            best[bvid] = r
    return rest + list(best.values())


def load_raw_records():
    """读取 Analyzer（主源）+ 本地 Finder（备份）合并为 canonical derived 列表。

    合并策略：以 kid 为键，Analyzer 优先；本地独有的记录（备份补齐）并入。
    返回 list[dict]，每项含规则引擎所需字段 + 展示字段 + source 标记。
    """
    analyzer = _read_analyzer(ANALYZER_DB)
    local = _read_local(LOCAL_DB)
    merged = dict(analyzer)  # 主源优先
    for kid, d in local.items():
        if kid not in merged:
            merged[kid] = d
    records = list(merged.values())
    # Plan A：对齐官网——同一视频只显示最近一次观看
    return _fold_latest_by_bvid(records)


# 模块级缓存：避免每请求重读 7 张年表
RAW_CACHE = {"records": None, "loaded_at": 0}


def reload_raw():
    RAW_CACHE["records"] = load_raw_records()
    RAW_CACHE["loaded_at"] = int(time.time())
    return RAW_CACHE["records"]


def get_raw():
    if RAW_CACHE["records"] is None:
        reload_raw()
    return RAW_CACHE["records"]


# ===================== 侧状态库（manual_skip / auto_exempt / meta） =====================

def _state_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    return sqlite3.connect(STATE_DB)


def _ensure_state_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS skip_state("
        "kid TEXT PRIMARY KEY, manual_skip INTEGER NOT NULL DEFAULT 0, "
        "auto_exempt INTEGER NOT NULL DEFAULT 0, "
        "archived INTEGER NOT NULL DEFAULT 0)"
    )
    # 迁移：旧库可能缺列（向前兼容）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info('skip_state')").fetchall()}
        if "archived" not in cols:
            conn.execute("ALTER TABLE skip_state ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    conn.execute(
        "CREATE TABLE IF NOT EXISTS saved_views("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL, "
        "created_at INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lists("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, "
        "values_json TEXT NOT NULL, created_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")


def load_state():
    """读取侧状态：{kid: {manual_skip, auto_exempt, archived}}。"""
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        rows = conn.execute(
            "SELECT kid, manual_skip, auto_exempt, archived FROM skip_state"
        ).fetchall()
        return {kid: {"manual_skip": ms, "auto_exempt": ae, "archived": ar}
                for kid, ms, ae, ar in rows}
    finally:
        conn.close()


def save_skip(kid, manual_skip=None, auto_exempt=None, archived=None):
    """写入单条侧状态（manual_skip / auto_exempt / archived 可单独更新）。"""
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        cur = conn.execute(
            "SELECT manual_skip, auto_exempt, archived FROM skip_state WHERE kid=?", (kid,)
        ).fetchone()
        ms = cur[0] if cur else 0
        ae = cur[1] if cur else 0
        ar = cur[2] if cur else 0
        if manual_skip is not None:
            ms = 1 if manual_skip else 0
        if auto_exempt is not None:
            ae = 1 if auto_exempt else 0
        if archived is not None:
            ar = 1 if archived else 0
        conn.execute(
            "INSERT INTO skip_state(kid, manual_skip, auto_exempt, archived) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(kid) DO UPDATE SET manual_skip=excluded.manual_skip, "
            "auto_exempt=excluded.auto_exempt, archived=excluded.archived",
            (kid, ms, ae, ar),
        )
        conn.commit()
    finally:
        conn.close()


def set_meta(key, value):
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_meta(key, default=None):
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


# ===================== 分类（基于 engine，确定性，不写源库） =====================

def classify_record(rec, rule, now, state):
    """对单条 canonical 记录计算分类字段，返回带分类的输出 dict（不修改源）。

    在原语义基础上新增 archived 列（来自侧库 archived_state 表，批量归档落库）。
    """
    prog = rec.get("progress")
    finished = (prog == -1)
    kid = rec.get("kid")
    m = state.get(kid) or {}
    manual_skip = bool(m.get("manual_skip"))
    auto_exempt = bool(m.get("auto_exempt"))
    archived = bool(m.get("archived"))
    auto_skip = 0
    reason = ""
    if not finished:
        res = engine.evaluate_rule(rec, rule, now)
        if res and not auto_exempt:
            action, label = res
            auto_skip = 1
            reason = f"{action}::{label}"
    if manual_skip:
        skip_state = "manual"
    elif auto_skip:
        skip_state = "auto"
    else:
        skip_state = ""
    if archived:
        skip_state = "archived" if not skip_state else skip_state
    out = {k: v for k, v in rec.items() if k != "source"}
    out.update({
        "finished": finished,
        "auto_skip": auto_skip,
        "auto_skip_reason": reason,
        "manual_skip": 1 if manual_skip else 0,
        "archived": 1 if archived else 0,
        "skip_state": skip_state,
    })
    return out


def _classify_all(raw_records, rule, now, state):
    return [classify_record(r, rule, now, state) for r in raw_records]


# ===================== 查询 / 视图 / 应用 / 状态 =====================

def _view_ok(r, view):
    """三视图 + 快捷视图筛选（保留分类核心语义）。"""
    if not view:
        return True
    if view == "all":
        return True
    if view == "finished":
        return bool(r["finished"])
    if view == "needs":
        if r["finished"]:
            return False
        if r["auto_skip"] != 0:
            return False
        # 与旧逻辑一致：≈看完（>=95%）视为不需要出现在「需要观看」
        dur = r.get("duration") or 0
        p = r.get("progress")
        if dur and p is not None and p >= 0 and p >= 0.95 * dur:
            return False
        return True
    if view == "skipped":
        return (r["auto_skip"] == 1 and
                r["auto_skip_reason"].startswith("auto_skip::"))
    if view == "stale":
        return (r["auto_skip"] == 1 and
                r["auto_skip_reason"].startswith("stale::"))
    if view == "archived":
        return bool(r.get("archived"))
    return True


def query(raw_records, rule, state, params):
    """统一查询入口：视图选择 + 高级筛选 spec + 全文搜索 + 排序 + 分页。

    params 支持：
      - view: 快捷视图 (all/finished/needs/skipped/stale/archived)
      - q: 全文搜索（标题/作者/UP主/bvid/kid）
      - filter: 高级筛选 spec（engine.evaluate_filter 所用 dict）
      - sort: [{field, dir}] 排序规范
      - limit / offset: 分页
    返回 (items, total, counts)。
    """
    now = int(time.time())
    classified = _classify_all(raw_records, rule, now, state)
    items, total = _filter(classified, params, now)
    counts = _view_counts(classified)
    return items, total, counts


def _filter(classified, params, now):
    view = (params.get("view") or "all").strip()
    q = (params.get("q") or "").strip()
    spec = params.get("filter")  # 高级筛选 spec dict（可空）
    sort_fields = params.get("sort") or []
    # 预构建 ctx：列表引用 + 聚合计数（供 having 用）
    lists = params.get("lists") or {}
    ctx_lists = {k: set(v) for k, v in lists.items()}
    group_counts = {}
    having = (spec or {}).get("having") if spec else None
    if having:
        gb = having.get("groupBy")
        if gb:
            from collections import Counter
            group_counts = dict(Counter(r.get(gb) for r in classified))
    ctx = {"lists": ctx_lists, "group_counts": group_counts}

    out = []
    for r in classified:
        if not _view_ok(r, view):
            continue
        if q:
            hay = " ".join(str(r.get(k) or "") for k in
                          ("title", "author_name", "bvid", "kid", "remark", "tag_name"))
            if q.lower() not in hay.lower():
                continue
        # 时间窗筛选（按 view_at 秒级时间戳）
        tf = params.get("time_from"); tt = params.get("time_to")
        if tf is not None or tt is not None:
            va = int(r.get("view_at") or 0)
            if tf is not None and va < int(tf):
                continue
            if tt is not None and va > int(tt):
                continue
        if spec:
            if not engine.evaluate_filter(r, spec, now, ctx):
                continue
        out.append(r)
    # 排序（稳定多列）
    if sort_fields:
        engine.apply_sort(out, sort_fields, now)
    else:
        out.sort(key=lambda x: x.get("view_at") or 0, reverse=True)
    total = len(out)
    limit = int(params.get("limit") or 60)
    offset = int(params.get("offset") or 0)
    return out[offset:offset + limit], total


def _view_counts(classified):
    finished = auto_skip = stale = needs = 0
    for r in classified:
        if r["finished"]:
            finished += 1
            continue
        if r["auto_skip"] == 1:
            if r["auto_skip_reason"].startswith("stale::"):
                stale += 1
            else:
                auto_skip += 1
        else:
            needs += 1
    return {
        "total": len(classified), "finished": finished,
        "auto_skip": auto_skip, "stale": stale, "needs": needs,
    }


def apply_rules(raw_records, rule, state, dry=False):
    """按当前 active 规则重扫：记录应用哈希，并清除被规则命中处的 manual_skip（auto 覆盖 manual）。

    分类是确定性计算的，apply 主要作用是固化「已应用」状态 + 清理冲突的 manual_skip。
    dry=True 仅计数不写库（供应用前确认）。
    """
    now = int(time.time())
    auto_set = stale_set = manual_cleared = 0
    for r in raw_records:
        kid = r.get("kid")
        if r.get("progress") == -1:
            continue
        res = engine.evaluate_rule(r, rule, now)
        if res:
            action, _ = res
            m = state.get(kid) or {}
            if m.get("manual_skip"):
                if not dry:
                    save_skip(kid, manual_skip=0)
                    state[kid] = {**m, "manual_skip": 0}
                manual_cleared += 1
            if action == "stale":
                stale_set += 1
            else:
                auto_set += 1
    return {
        "ok": True, "total": len(raw_records), "auto_set": auto_set,
        "stale_set": stale_set, "manual_cleared": manual_cleared, "dry": dry,
    }


def rules_status(raw_records, rule, state, rules):
    """返回规则应用状态：pending（待应用）+ dirty_count（自上次应用以来新增未套用）。"""
    current_hash = engine.rules_hash(rules)
    applied_hash = get_meta("rule_applied_hash", "")
    pending = bool(applied_hash) and applied_hash != current_hash
    last_apply_at = get_meta("last_rule_apply_at", "")
    now = int(time.time())
    dirty_count = 0
    if last_apply_at:
        la = int(last_apply_at)
        for r in raw_records:
            va = r.get("view_at") or 0
            if va > la and r.get("progress") != -1:
                m = state.get(r.get("kid")) or {}
                manual = bool(m.get("manual_skip"))
                if manual:
                    continue
                res = engine.evaluate_rule(r, rule, now)
                if res and not m.get("auto_exempt"):
                    continue  # 已被规则命中，不算脏
                dirty_count += 1
    return {
        "ok": True,
        "current_hash": current_hash,
        "applied_hash": applied_hash,
        "pending": pending,
        "dirty_count": dirty_count,
        "last_apply_at": int(last_apply_at) if last_apply_at else None,
    }


def get_meta_banner(raw_records):
    """常驻状态文字：主源 / 备份源 / 合并条数。"""
    analyzer_n = sum(1 for r in raw_records if r.get("source") == "analyzer")
    local_n = sum(1 for r in raw_records if r.get("source") == "local")
    return {
        "total": len(raw_records),
        "analyzer": analyzer_n,
        "local_backup": local_n,
        "analyzer_db": ANALYZER_DB if os.path.exists(ANALYZER_DB) else None,
    }


# ===================== 保存视图 / 黑白名单 / 批量操作 =====================

def save_view(view_id, name, spec):
    """保存一个筛选视图（覆盖式）。view_id 为空时自动生成。"""
    import uuid
    vid = view_id or ("view_" + uuid.uuid4().hex[:8])
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute(
            "INSERT INTO saved_views(id, name, spec, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, spec=excluded.spec",
            (vid, name, json.dumps(spec, ensure_ascii=False), int(time.time())),
        )
        conn.commit()
        return vid
    finally:
        conn.close()


def list_views():
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        rows = conn.execute(
            "SELECT id, name, spec, created_at FROM saved_views ORDER BY created_at DESC"
        ).fetchall()
        return [{"id": r[0], "name": r[1],
                 "spec": json.loads(r[2]), "created_at": r[3]} for r in rows]
    finally:
        conn.close()


def delete_view(view_id):
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute("DELETE FROM saved_views WHERE id=?", (view_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def save_list(list_id, name, kind, values):
    """保存黑白名单。kind='whitelist'|'blacklist'；values=list[str]。"""
    import uuid
    lid = list_id or ("list_" + uuid.uuid4().hex[:8])
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute(
            "INSERT INTO lists(id, name, kind, values_json, created_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "kind=excluded.kind, values_json=excluded.values_json",
            (lid, name, kind, json.dumps(values, ensure_ascii=False), int(time.time())),
        )
        conn.commit()
        return lid
    finally:
        conn.close()


def list_lists():
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        rows = conn.execute(
            "SELECT id, name, kind, values_json, created_at FROM lists ORDER BY created_at DESC"
        ).fetchall()
        return [{"id": r[0], "name": r[1], "kind": r[2],
                 "values": json.loads(r[3]), "created_at": r[4]} for r in rows]
    finally:
        conn.close()


def delete_list(list_id):
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute("DELETE FROM lists WHERE id=?", (list_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def batch_op(kids, action):
    """批量操作：archive(归档) / unarchive(取消归档) / skip(标记跳过) /
    unskip(取消跳过) / exempt(豁免自动跳过) / unexempt(取消豁免)。
    返回受影响的 kid 数。
    """
    affected = 0
    for kid in kids:
        if action == "archive":
            save_skip(kid, archived=1)
        elif action == "unarchive":
            save_skip(kid, archived=0)
        elif action == "skip":
            save_skip(kid, manual_skip=1)
        elif action == "unskip":
            save_skip(kid, manual_skip=0)
        elif action == "exempt":
            save_skip(kid, auto_exempt=1)
        elif action == "unexempt":
            save_skip(kid, auto_exempt=0)
        else:
            continue
        affected += 1
    return affected
