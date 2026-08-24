#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站历史记录查看器 — Phase 2 本地 Web 服务（零依赖：仅标准库）

提供：
- 类 B站历史页（封面 + 标题 + UP主 + 时间 + BV，可点击）
- 关键词搜索 + 类型筛选 + 时间范围筛选
- 「需要观看 / 已跳过 / 已搁置」三视图（基于 auto_skip + auto_skip_reason）
- 自定义续看规则引擎（data/rules.json，单条 active + 多预设，含冲突校验）
- 封面本地缓存（仅缺文件时下载）
- 一键同步（后台调用 collector 重新拉取，全量以捕获删除/封面）

用法：
    python server.py              # 启动 http://127.0.0.1:8765
    python server.py --port 9000
"""
import configparser
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector  # 复用 DEFAULT_DB / DEFAULT_CONFIG / UA / REFERER

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
PROJECT_ROOT = collector.PROJECT_ROOT
COVERS_DIR = os.path.join(PROJECT_ROOT, "data", "covers")
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "sync_progress.json")
RESULT_FILE = os.path.join(PROJECT_ROOT, "data", "sync_result.json")
RULES_FILE = os.path.join(PROJECT_ROOT, "data", "rules.json")
FILTERS_INI = os.path.join(PROJECT_ROOT, "data", "filters.ini")  # 仅用于首次迁移，随后删除
AUTOSKIP_PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "auto_skip_progress.json")

# 同步状态（后台线程写，前端轮询读）
sync_state = {"running": False, "last": None, "started_at": 0}
# 自动跳过应用状态（后台线程写，前端轮询读）
autoskip_state = {"running": False, "last": None, "started_at": 0}

ALL_BUSINESS = ["archive", "pgc", "article", "live"]


# ===================== 续看规则引擎（data/rules.json） =====================

def default_rules():
    """随软件下发的默认续看规则（全部可调）。"""
    return {"rules": [
        {
            "id": "default",
            "name": "默认续看规则",
            "active": True,
            "match": "any",
            "groups": [
                {"label": "接近看完", "action": "auto_skip", "conditions": [
                    {"field": "progress_pct", "op": ">=", "value": 0.95}]},
                {"label": "误触-短暂观看", "action": "auto_skip", "conditions": [
                    {"field": "progress_pct", "op": "<", "value": 0.03},
                    {"field": "progress_sec", "op": "<", "value": 15}]},
                {"label": "短视频碎片", "action": "auto_skip", "conditions": [
                    {"field": "duration", "op": "<", "value": 60}]},
                {"label": "直播回放", "action": "auto_skip", "conditions": [
                    {"field": "business", "op": "in", "value": "live"}]},
                {"label": "陈旧-久未观看", "action": "stale", "conditions": [
                    {"field": "view_at_age_days", "op": ">", "value": 90}]},
            ],
        }
    ]}


def _migrate_legacy_filters():
    """读旧 filters.ini，转成遗留预设规则（active=False），并删除 ini。

    旧语义：business 不在白名单→跳过；duration>=min_dur*60→跳过；author 在名单→跳过。
    仅当对应条件非平凡时才生成分组（避免 min_dur=0 时「duration>=0」误杀全部）。
    """
    legacy = None
    if os.path.exists(FILTERS_INI):
        try:
            cfg = configparser.ConfigParser()
            cfg.read(FILTERS_INI, encoding="utf-8")
            biz, md, authors = [], 0, []
            if cfg.has_section("filters"):
                b = cfg.get("filters", "business", fallback="")
                if b.strip():
                    biz = [x.strip() for x in b.split(",") if x.strip()]
                try:
                    md = int(cfg.get("filters", "min_duration_min", fallback="0") or 0)
                except ValueError:
                    md = 0
                a = cfg.get("filters", "authors", fallback="")
                if a.strip():
                    authors = [x.strip() for x in a.split(",") if x.strip()]
            groups = []
            if biz and set(biz) != set(ALL_BUSINESS):
                groups.append({"label": "类型排除", "action": "auto_skip", "conditions": [
                    {"field": "business", "op": "not_in", "value": ",".join(biz)}]})
            if md > 0:
                groups.append({"label": "时长下限", "action": "auto_skip", "conditions": [
                    {"field": "duration", "op": ">=", "value": md * 60}]})
            if authors:
                groups.append({"label": "UP主排除", "action": "auto_skip", "conditions": [
                    {"field": "author_name", "op": "in", "value": ",".join(authors)}]})
            if groups:
                legacy = {
                    "id": "legacy-filters",
                    "name": "遗留：旧版筛选(filters.ini)",
                    "active": False, "match": "any", "groups": groups,
                }
        except Exception:
            pass
        try:
            os.remove(FILTERS_INI)
        except Exception:
            pass
    return legacy


def ensure_rules():
    """确保 rules.json 存在：首次启动由默认规则 + 旧 ini 迁移生成。

    废弃 filters.ini：一旦 rules.json 就绪，遗留的 ini 文件直接删除（§14 决策）。
    """
    if os.path.exists(RULES_FILE):
        # 规则已就绪 → 顺手清理遗留 ini（即使之前早退未删）
        if os.path.exists(FILTERS_INI):
            try:
                os.remove(FILTERS_INI)
            except Exception:
                pass
        return
    rules = default_rules()
    legacy = _migrate_legacy_filters()
    if legacy:
        rules["rules"].append(legacy)
    _write_json_file(RULES_FILE, rules)


def load_rules():
    ensure_rules()
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("rules"), list):
            d = default_rules()
    except Exception:
        d = default_rules()
    # 保底：至少一条 active
    if not any(r.get("active") for r in d.get("rules", [])):
        if d.get("rules"):
            d["rules"][0]["active"] = True
    return d


def save_rules(rules):
    _write_json_file(RULES_FILE, rules)


def get_active_rule(rules):
    for r in rules.get("rules", []):
        if r.get("active"):
            return r
    return None


def _derived(rec, now):
    """从固有字段派生规则可用旋钮（progress_pct / progress_sec / view_at_age_days）。"""
    prog = rec.get("progress")
    dur = rec.get("duration")
    d = {}
    if prog == -1:
        d["progress_pct"] = 1.0
        d["progress_sec"] = dur if dur else 0
    elif prog is not None and dur and dur > 0:
        d["progress_pct"] = prog / dur
        d["progress_sec"] = prog
    else:
        d["progress_pct"] = None
        d["progress_sec"] = prog if prog is not None else None
    va = rec.get("view_at")
    d["view_at_age_days"] = (now - va) / 86400 if va else None
    return d


def eval_condition(rec, cond, now):
    field = cond.get("field")
    op = cond.get("op")
    val = cond.get("value")
    der = _derived(rec, now)
    if field == "business":
        cur = rec.get("business")
    elif field == "author_name":
        cur = rec.get("author_name") or ""
    elif field == "author_mid":
        cur = rec.get("author_mid") or ""
    elif field == "title":
        cur = rec.get("title") or ""
    elif field == "duration":
        cur = rec.get("duration")
    elif field == "archived_only":
        cur = rec.get("archived_only")
    elif field == "progress_pct":
        cur = der["progress_pct"]
    elif field == "progress_sec":
        cur = der["progress_sec"]
    elif field == "view_at_age_days":
        cur = der["view_at_age_days"]
    else:
        cur = None

    if op in ("in", "not_in"):
        if isinstance(val, list):
            vals = [str(v).strip() for v in val if str(v).strip()]
        else:
            vals = [v.strip() for v in str(val).split(",") if v.strip()]
        inset = cur in vals if cur is not None else False
        return inset if op == "in" else (not inset)
    if op == "contains":
        return bool(cur) and str(val) in str(cur)
    if op == "==":
        return cur == val
    if cur is None:
        return False
    try:
        if op == "between":
            a, b = [float(x) for x in str(val).split("-")]
            return a <= float(cur) <= b
        cv = float(cur)
        v = float(val)
        if op == ">=":
            return cv >= v
        if op == "<=":
            return cv <= v
        if op == ">":
            return cv > v
        if op == "<":
            return cv < v
    except Exception:
        return False
    return False


def eval_group(rec, g, now):
    for c in g.get("conditions", []):
        if not eval_condition(rec, c, now):
            return False
    return True


def evaluate_rule(rec, rule, now):
    """返回 (action, label) 或 None。已看完(progress=-1)为固有状态，规则不可纳入。"""
    if rec.get("progress") == -1:
        return None
    groups = rule.get("groups", [])
    matched = [g for g in groups if eval_group(rec, g, now)]
    if not matched:
        return None
    if rule.get("match", "any") == "all" and len(matched) < len(groups):
        return None
    g = matched[0]
    return (g.get("action", "auto_skip"), g.get("label", ""))


def validate_rule(rule):
    """冲突校验：hard=阻断, soft=警告, info=提示。"""
    hard, soft, info = [], [], []
    for g in rule.get("groups", []):
        conds = g.get("conditions", [])
        byfield = {}
        for c in conds:
            byfield.setdefault(c.get("field"), []).append(c)
        for f, lst in byfield.items():
            ge = []
            le = []
            for c in lst:
                try:
                    if c.get("op") == ">=":
                        ge.append(float(c.get("value")))
                    elif c.get("op") == ">":
                        ge.append(float(c.get("value")) + 1e-9)
                    elif c.get("op") == "<=":
                        le.append(float(c.get("value")))
                    elif c.get("op") == "<":
                        le.append(float(c.get("value")) - 1e-9)
                except Exception:
                    pass
            for a in ge:
                for b in le:
                    if a >= b:
                        hard.append(f"分组「{g.get('label')}」同一字段 {f} 既要求 ≥{a} 又要求 ≤{b}，"
                                    f"无记录可同时满足（C1 冲突）")
            lt = [(float(c.get("value")), c) for c in lst if c.get("op") == "<"]
            ge2 = [(float(c.get("value")), c) for c in lst if c.get("op") == ">="]
            for v1, c1 in lt:
                for v2, c2 in lt:
                    if c1 is not c2 and v1 <= v2:
                        soft.append(f"分组「{g.get('label')}」{f} <{v1} 已被 <{v2} 包含，后者冗余（C2）")
            for v1, c1 in ge2:
                for v2, c2 in ge2:
                    if c1 is not c2 and v1 >= v2:
                        soft.append(f"分组「{g.get('label')}」{f} ≥{v1} 已包含 ≥{v2}，后者冗余（C2）")
        for c in conds:
            if c.get("field") in ("progress_pct", "progress_sec"):
                try:
                    v = float(c.get("value"))
                    if c.get("op") in ("<=",) and v >= 1.0:
                        hard.append(f"分组「{g.get('label')}」进度条件 {c.get('op')}{v} 会把「已看完」"
                                    f"纳入跳过，违反固有状态不可反转（C3）")
                except Exception:
                    pass
    return {"hard": hard, "soft": soft, "info": info}


def run_apply_rules(dry=False):
    """对全量数据套用当前 active 规则。dry=True 只计数不落库（用于应用前确认）。

    写入约定（auto_skip 与 auto_skip_reason 绑定共存）：
    - 命中分组 → auto_skip=1, auto_skip_reason="<action>::<label>", 清 manual_skip（auto 覆盖 manual）
    - 未命中 → auto_skip=0, auto_skip_reason=""，保留 manual_skip
    - 已看完(progress=-1) → 永不被跳过：auto_skip=0, reason=""，保留 manual_skip
    """
    rules = load_rules()
    rule = get_active_rule(rules)
    if not rule:
        return {"ok": False, "err": "没有激活的规则"}
    db = get_db_path()
    conn = sqlite3.connect(db)
    try:
        now = int(time.time())
        rows = conn.execute(
            "SELECT kid, business, duration, author_name, author_mid, title, progress, view_at, "
            "manual_skip, auto_skip, auto_skip_reason, archived_only FROM history"
        ).fetchall()
        total = len(rows)
        auto_set = stale_set = manual_cleared = 0
        updates = []
        for r in rows:
            (kid, business, duration, author_name, author_mid, title, progress, view_at,
             ms, as_, reason, archived) = r
            rec = {
                "business": business, "duration": duration, "author_name": author_name,
                "author_mid": author_mid, "title": title, "progress": progress,
                "view_at": view_at, "archived_only": archived,
            }
            if progress == -1:
                if as_ != 0 or reason:
                    updates.append((0, "", ms, kid))
                continue
            res = evaluate_rule(rec, rule, now)
            if res:
                action, label = res
                new_reason = f"{action}::{label}"
                if as_ != 1 or reason != new_reason or ms != 0:
                    updates.append((1, new_reason, 0, kid))
                    auto_set += 1
                    if action == "stale":
                        stale_set += 1
                    if ms == 1:
                        manual_cleared += 1
            else:
                if as_ != 0 or reason != "":
                    updates.append((0, "", ms, kid))
        if not dry:
            for (nas, nr, nms, kid) in updates:
                conn.execute(
                    "UPDATE history SET auto_skip=?, auto_skip_reason=?, manual_skip=? WHERE kid=?",
                    (nas, nr, nms, kid),
                )
            conn.commit()
        return {
            "ok": True, "total": total, "auto_set": auto_set, "stale_set": stale_set,
            "manual_cleared": manual_cleared, "dry": dry,
        }
    finally:
        conn.close()


# ===================== 既有工具 =====================

def _write_json_file(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _job_running():
    """是否有同步或自动跳过任务在跑（用于并发互斥，避免同时写库）。"""
    return sync_state["running"] or autoskip_state["running"]


def get_db_path():
    try:
        cfg = collector.load_config(collector.DEFAULT_CONFIG)
    except Exception:
        cfg = {}
    db = cfg.get("db_path") or collector.DEFAULT_DB
    if not os.path.isabs(db):
        db = os.path.join(PROJECT_ROOT, db)
    return db


def get_web_port():
    try:
        cfg = collector.load_config(collector.DEFAULT_CONFIG)
        return int(cfg.get("web_port", 8765))
    except Exception:
        return 8765


def human_dt(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def get_sync_meta():
    """读取数据版本 / 最后成功同步时间 / 总条数，供前端常驻文字展示。"""
    meta = {"version": None, "last_success_at": None, "total": 0}
    db = get_db_path()
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='sync_version'").fetchone()
            if row:
                meta["version"] = int(row[0])
            row = conn.execute("SELECT value FROM meta WHERE key='last_success_at'").fetchone()
            if row:
                meta["last_success_at"] = int(row[0])
            row = conn.execute("SELECT COUNT(*) FROM history").fetchone()
            meta["total"] = row[0]
        finally:
            conn.close()
    return meta


def read_progress_file():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def bump_sync_meta():
    """同步成功后递增数据版本并记录最后成功时间。"""
    db = get_db_path()
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='sync_version'").fetchone()
        ver = int(row[0]) if row else 0
        ver += 1
        now = int(time.time())
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('sync_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(ver),),
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('last_success_at',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(now),),
        )
        conn.commit()
    finally:
        conn.close()


def get_meta(key, default=None):
    db = get_db_path()
    if not os.path.exists(db):
        return default
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def set_meta(key, value):
    db = get_db_path()
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def _rules_hash():
    import hashlib
    rules = load_rules()
    cur = json.dumps(rules, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(cur.encode("utf-8")).hexdigest()


def _store_applied_rules():
    """应用成功后记录『已应用规则哈希』与『最后应用时间』，供前端『待应用/脏』角标。"""
    set_meta("rule_applied_hash", _rules_hash())
    set_meta("last_rule_apply_at", int(time.time()))


def rules_status():
    """返回规则应用状态：待应用(pending) + 自上次应用以来可能命中却未标的脏记录数(dirty_count)。"""
    current_hash = _rules_hash()
    applied_hash = get_meta("rule_applied_hash", "")
    pending = bool(applied_hash) and applied_hash != current_hash
    last_apply_at = get_meta("last_rule_apply_at", "")
    dirty_count = 0
    db = get_db_path()
    if os.path.exists(db) and last_apply_at:
        conn = sqlite3.connect(db)
        try:
            dirty_count = conn.execute(
                "SELECT COUNT(*) FROM history "
                "WHERE view_at > ? AND progress != -1 AND auto_skip=0 AND manual_skip=0",
                (int(last_apply_at),),
            ).fetchone()[0]
        finally:
            conn.close()
    return {
        "ok": True,
        "current_hash": current_hash,
        "applied_hash": applied_hash,
        "pending": pending,
        "dirty_count": dirty_count,
        "last_apply_at": int(last_apply_at) if last_apply_at else None,
    }


def ensure_initial_meta():
    """服务启动时，若已有数据但无版本记录，把初始载入记为 v1。"""
    db = get_db_path()
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        has_ver = conn.execute("SELECT 1 FROM meta WHERE key='sync_version'").fetchone()
        if cnt > 0 and not has_ver:
            conn.execute("INSERT INTO meta(key,value) VALUES('sync_version','1')")
            lr = conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
            if lr:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES('last_success_at',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (lr[0],),
                )
            conn.commit()
    finally:
        conn.close()


def cover_ext(url):
    if not url:
        return "jpg"
    p = urllib.parse.urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if p.endswith(ext):
            return "jpg" if ext == ".jpeg" else ext.lstrip(".")
    return "jpg"


def ensure_cover(kid, url):
    """返回本地封面文件路径；不存在则从远程下载并缓存。"""
    os.makedirs(COVERS_DIR, exist_ok=True)
    ext = cover_ext(url)
    path = os.path.join(COVERS_DIR, f"{kid}.{ext}")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    if not url:
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": collector.UA, "Referer": collector.REFERER}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception:
        return None


def build_query(params):
    wheres = []
    args = []
    q = (params.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        wheres.append("(title LIKE ? OR author_name LIKE ? OR bvid LIKE ? OR kid LIKE ?)")
        args.extend([like, like, like, like])
    business = (params.get("business") or "").strip()
    if business:
        wheres.append("business = ?")
        args.append(business)

    # 视图：needs（需要观看）/ skipped（已跳过）/ stale（已搁置）/ 空（完整历史）
    view = (params.get("view") or "").strip()
    if view == "needs" or (params.get("needs_watching") == "1" and not view):
        # 「需要观看」= 未完成 且 未被规则自动跳过（auto_skip=0）。
        # 手动跳过（manual_skip=1）保留可见，以便在「需要观看」视图下直接反悔取消（§13.5）；
        # 自动跳过按规则隐藏，需到完整历史/已跳过/已搁置视图或批量「恢复」模式取消。
        wheres.append("(auto_skip = 0)")
        wheres.append(
            "NOT (progress = -1 OR "
            "(progress IS NOT NULL AND duration IS NOT NULL AND progress >= 0.95 * duration))"
        )
    elif view == "skipped":
        wheres.append("auto_skip = 1")
        wheres.append("auto_skip_reason LIKE 'auto_skip::%'")
    elif view == "stale":
        wheres.append("auto_skip = 1")
        wheres.append("auto_skip_reason LIKE 'stale::%'")

    if params.get("archived_only") == "1":
        wheres.append("archived_only = 1")
    date_from = params.get("date_from")
    date_to = params.get("date_to")
    if date_from and date_from.isdigit():
        wheres.append("view_at >= ?")
        args.append(int(date_from))
    if date_to and date_to.isdigit():
        wheres.append("view_at <= ?")
        args.append(int(date_to))
    # 时长筛选（秒）
    dur_min = params.get("duration_min")
    dur_max = params.get("duration_max")
    if dur_min and dur_min.isdigit():
        wheres.append("duration >= ?")
        args.append(int(dur_min))
    if dur_max and dur_max.isdigit():
        wheres.append("duration <= ?")
        args.append(int(dur_max))
    # 设备筛选（dt 值，从 raw_json 提取）[V2]
    dt = (params.get("dt") or "").strip()
    if dt:
        wheres.append("CAST(json_extract(raw_json, '$.history.dt') AS INTEGER) = ?")
        args.append(int(dt))
    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
    order = "view_at DESC"
    return where_sql, args, order


def fetch_history(params):
    db = get_db_path()
    if not os.path.exists(db):
        return {"items": [], "total": 0, "error": "数据库不存在，请先运行 collector.py 拉取"}
    limit = int(params.get("limit") or 60)
    offset = int(params.get("offset") or 0)
    where_sql, args, order = build_query(params)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM history{where_sql}", args
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT kid, title, author_name, author_mid, view_at, bvid, business, "
            f"cover, progress, duration, uri, archived_only, manual_skip, auto_skip, "
            f"auto_skip_reason, "
            f"CASE WHEN manual_skip = 1 THEN 'manual' "
            f"     WHEN auto_skip = 1 THEN 'auto' ELSE '' END AS skip_state, "
            f"json_extract(raw_json, '$.history.dt') AS dt, "
            f"json_extract(raw_json, '$.live_status') AS live_status "
            f"FROM history{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    return {"items": items, "total": total}


def _has_baseline():
    """是否已有同步基线：last_sync 已记录且库非空 → 可走增量。"""
    db = get_db_path()
    if not os.path.exists(db):
        return False
    conn = sqlite3.connect(db)
    try:
        ls = conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        cnt = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    finally:
        conn.close()
    return bool(ls and cnt > 0)


def run_sync_background(full=False):
    # §12.1：有基线默认增量（快、不重复拉全量）；无基线自动全量建基线；full=强制全量重建
    incremental = (not full) and _has_baseline()
    def _job():
        sync_state["running"] = True
        sync_state["started_at"] = int(time.time())
        # 清掉上一轮完成标记，避免前端误读旧结论
        try:
            if os.path.exists(RESULT_FILE):
                os.remove(RESULT_FILE)
        except Exception:
            pass
        cmd = [sys.executable, os.path.join(HERE, "collector.py")]
        if full:
            cmd.append("--full")
        elif incremental:
            cmd.append("--incremental")
        try:
            subprocess.run(cmd, cwd=PROJECT_ROOT, timeout=900)
            # 以 collector 写出的完成结论为准，而非子进程退出码
            res = None
            if os.path.exists(RESULT_FILE):
                try:
                    with open(RESULT_FILE, "r", encoding="utf-8") as f:
                        res = json.load(f)
                except Exception:
                    res = None
            completed = bool(res and res.get("completed"))
            if completed:
                bump_sync_meta()  # 只有真正完整拉取才更新数据版本
                sync_state["last"] = {
                    "ok": True,
                    "at": int(time.time()),
                    "fetched": (res or {}).get("fetched"),
                    "mode": "full" if full else ("incremental" if incremental else "auto"),
                }
            else:
                reason = (res or {}).get("error") or "同步未完成"
                sync_state["last"] = {"ok": False, "at": int(time.time()), "err": reason}
        except subprocess.TimeoutExpired:
            sync_state["last"] = {"ok": False, "at": int(time.time()), "err": "同步超时（>900s）"}
        except Exception as e:  # noqa
            sync_state["last"] = {"ok": False, "at": int(time.time()), "err": str(e)}
        finally:
            sync_state["running"] = False

    t = threading.Thread(target=_job, daemon=True)
    t.start()


def apply_rules_background(dry=False):
    """按当前 active 规则对全量数据重扫 auto_skip（§14 续看规则引擎）。

    - 命中分组 → auto_skip=1 且 auto_skip_reason 绑定写入；清 manual_skip（auto 覆盖 manual）。
    - 未命中 → auto_skip=0、reason 清空（保留 manual_skip）。
    - 带进度屏蔽：运行期间 autoskip_state.running=True，前端禁用按钮并轮询进度；
      与同步任务互斥（避免同时写库）。
    """
    def _job():
        autoskip_state["running"] = True
        autoskip_state["started_at"] = int(time.time())
        _write_json_file(AUTOSKIP_PROGRESS_FILE, {
            "phase": "start", "done": 0, "total": 0, "completed": False,
            "dry": dry, "updated_at": int(time.time()),
        })
        try:
            res = run_apply_rules(dry=dry)
            if not res.get("ok"):
                autoskip_state["last"] = {"ok": False, "err": res.get("err", "规则应用失败")}
                _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                    "phase": "error", "done": 0, "total": 0, "completed": False,
                    "error": res.get("err", ""), "updated_at": int(time.time()),
                })
                return
            if dry:
                autoskip_state["last"] = {"ok": True, "dry": True, **res}
                _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                    "phase": "done", "done": res["total"], "total": res["total"],
                    "completed": True, "dry": True, "updated_at": int(time.time()), **res,
                })
            else:
                autoskip_state["last"] = {
                    "ok": True, "at": int(time.time()),
                    "auto_set": res["auto_set"], "stale_set": res["stale_set"],
                    "manual_cleared": res["manual_cleared"], "total": res["total"],
                }
                _store_applied_rules()  # 记录已应用哈希 + 时间，供『待应用/脏』角标
                _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                    "phase": "done", "done": res["total"], "total": res["total"],
                    "completed": True, "auto_set": res["auto_set"],
                    "stale_set": res["stale_set"], "manual_cleared": res["manual_cleared"],
                    "updated_at": int(time.time()),
                })
        except Exception as e:  # noqa
            autoskip_state["last"] = {"ok": False, "at": int(time.time()), "err": str(e)}
            _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                "phase": "error", "done": 0, "total": 0,
                "completed": False, "error": str(e), "updated_at": int(time.time()),
            })
        finally:
            autoskip_state["running"] = False

    t = threading.Thread(target=_job, daemon=True)
    t.start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默默认访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            self._send(404, {"error": "not found"})
            return
        self._send(200, data, ctype)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        flat = {k: (v[0] if v else "") for k, v in qs.items()}

        if path in ("/", "/index.html"):
            self._serve_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            name = os.path.basename(path)
            fpath = os.path.join(WEB_DIR, name)
            if name.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            elif name.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            else:
                ctype = "application/octet-stream"
            self._serve_file(fpath, ctype)
            return
        if path == "/api/history":
            self._send(200, fetch_history(flat))
            return
        if path == "/api/sync":
            self._send(200, {
                "running": sync_state["running"],
                "progress": read_progress_file(),
                "last": sync_state["last"],
                "started_at": sync_state["started_at"],
                "meta": get_sync_meta(),
            })
            return
        if path == "/api/rules":
            self._send(200, load_rules())
            return
        if path == "/api/rules-dry":
            # 应用前 dry-run：返回计数（auto_set / stale_set / manual_cleared）供确认
            try:
                self._send(200, run_apply_rules(dry=True))
            except Exception as e:  # noqa
                self._send(200, {"ok": False, "err": str(e)})
            return
        if path == "/api/rules-status":
            self._send(200, rules_status())
            return
        if path == "/api/auto-skip-status":
            prog = None
            if os.path.exists(AUTOSKIP_PROGRESS_FILE):
                try:
                    with open(AUTOSKIP_PROGRESS_FILE, "r", encoding="utf-8") as f:
                        prog = json.load(f)
                except Exception:
                    prog = None
            self._send(200, {
                "running": autoskip_state["running"],
                "progress": prog,
                "last": autoskip_state["last"],
            })
            return
        if path.startswith("/cover/"):
            kid = urllib.parse.unquote(path[len("/cover/"):])
            db = get_db_path()
            url = None
            if os.path.exists(db):
                conn = sqlite3.connect(db)
                try:
                    row = conn.execute(
                        "SELECT cover FROM history WHERE kid=?", (kid,)
                    ).fetchone()
                    if row:
                        url = row[0]
                finally:
                    conn.close()
            local = ensure_cover(kid, url) if url else None
            if local and os.path.exists(local):
                ext = os.path.splitext(local)[1].lower()
                ctype = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp",
                    ".gif": "image/gif",
                }.get(ext, "image/jpeg")
                self._serve_file(local, ctype)
            else:
                self._send(404, {"error": "cover not available"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}") if raw else {}
        except Exception:
            body = {}

        if parsed.path == "/api/sync":
            full = qs.get("full", ["0"])[0] == "1"
            if sync_state["running"] or autoskip_state["running"]:
                self._send(200, {"started": False, "blocked": True,
                                 "reason": "已有同步或规则应用任务进行中"})
            else:
                try:
                    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                        json.dump({
                            "phase": "start", "page": 0, "fetched": 0,
                            "completed": False, "is_end": False,
                            "total_estimate": None, "updated_at": int(time.time()),
                            "mode": "full" if full else None,
                        }, f)
                except Exception:
                    pass
                run_sync_background(full=full)
                self._send(200, {"started": True, "running": True})
            return

        if parsed.path == "/api/rules":
            # 持久化规则（先校验冲突；再强制单条 active）
            rules = body if isinstance(body, dict) and "rules" in body else None
            if not rules:
                self._send(400, {"ok": False, "error": "无效的规则数据"})
                return
            rule_list = rules.get("rules", [])
            # C6：至多一条 active，且至少一条 active
            active_count = sum(1 for r in rule_list if r.get("active"))
            if active_count > 1:
                self._send(200, {"ok": False, "conflicts": {
                    "hard": ["存在多条 active 规则，v1 仅允许一条激活（C6）。请先停用其他规则。"],
                    "soft": [], "info": [],
                }})
                return
            if active_count == 0:
                self._send(200, {"ok": False, "conflicts": {
                    "hard": ["没有激活的规则，请至少激活一条（C6）。"],
                    "soft": [], "info": [],
                }})
                return
            # 逐规则冲突校验
            all_hard, all_soft = [], []
            for r in rule_list:
                v = validate_rule(r)
                all_hard.extend(v["hard"])
                all_soft.extend(v["soft"])
            if all_hard:
                self._send(200, {"ok": False, "conflicts": {
                    "hard": all_hard, "soft": all_soft, "info": [],
                }})
                return
            save_rules(rules)
            self._send(200, {"ok": True, "rules": load_rules(), "warnings": all_soft})
            return

        if parsed.path == "/api/rules-dry":
            # 应用前 dry-run：返回计数（auto_set / stale_set / manual_cleared）供确认
            try:
                res = run_apply_rules(dry=True)
                self._send(200, res)
            except Exception as e:  # noqa
                self._send(200, {"ok": False, "err": str(e)})
            return

        if parsed.path == "/api/apply-rules":
            # 按当前 active 规则全量重扫 auto_skip；带进度屏蔽、与同步互斥
            if sync_state["running"] or autoskip_state["running"]:
                self._send(200, {"started": False, "blocked": True,
                                 "reason": "已有同步或规则应用任务进行中，请稍后再试"})
            else:
                apply_rules_background(dry=False)
                self._send(200, {"started": True, "running": True})
            return

        if parsed.path == "/api/skip":
            kid = qs.get("kid", [""])[0]
            val = 1 if qs.get("value", ["1"])[0] in ("1", "true", "on") else 0
            kind = qs.get("kind", ["manual"])[0]  # manual=手动标记；auto=取消自动跳过
            if kid:
                db = get_db_path()
                if os.path.exists(db):
                    conn = sqlite3.connect(db)
                    try:
                        if kind == "auto":
                            # 取消自动跳过：清 auto_skip 与 reason，不动 manual_skip
                            conn.execute(
                                "UPDATE history SET auto_skip=0, auto_skip_reason='' WHERE kid=?",
                                (kid,),
                            )
                        else:
                            if val:
                                conn.execute(
                                    "UPDATE history SET manual_skip=1, auto_skip=0, "
                                    "auto_skip_reason='' WHERE kid=?", (kid,))
                            else:
                                conn.execute(
                                    "UPDATE history SET manual_skip=0 WHERE kid=?", (kid,))
                        conn.commit()
                    finally:
                        conn.close()
                    self._send(200, {"ok": True, "kid": kid, "value": val, "kind": kind})
                    return
            self._send(400, {"error": "invalid kid"})
            return
        self._send(404, {"error": "not found"})


def main():
    port = get_web_port()
    # 确保已存在数据库的结构与新代码一致（迁移 auto_skip_reason 等新列；不创建空白库）
    db = get_db_path()
    if os.path.exists(db):
        try:
            collector.init_db(db).close()
        except Exception:
            pass
    ensure_initial_meta()
    ensure_rules()  # 首次启动：生成 rules.json（含旧 filters.ini 迁移并删除 ini）
    # 首次启动自动校准一次：若从未应用过规则，按默认规则跑一遍，使『待应用/脏』角标初始为干净态
    if not get_meta("rule_applied_hash"):
        try:
            r = run_apply_rules(dry=False)
            if r.get("ok"):
                _store_applied_rules()
                print("[B站历史查看器] 首次启动：已按默认规则自动校准 auto_skip")
        except Exception as e:  # noqa
            print(f"[B站历史查看器] 首次自动校准失败：{e}")
    # 双栈绑定：依次尝试 :: (IPv6 通吃 localhost/IPv4 映射) -> 0.0.0.0 (IPv4 通吃) -> 127.0.0.1
    # 这样浏览器用 localhost / 127.0.0.1 / 局域网 IP 都能连上
    server = None
    bind_host = None
    last_err = None
    for _host in ("::", "0.0.0.0", "127.0.0.1"):
        try:
            server = ThreadingHTTPServer((_host, port), Handler)
            bind_host = _host
            break
        except OSError as e:
            last_err = e
            continue
    if server is None:
        e = last_err
        # errno 98 (Linux/Mac) / 10048 (Windows) / 48 (macOS 另一种) 均表示地址已被占用
        if e.errno in (98, 10048, 48) or getattr(e, "winerror", None) == 10048:
            print(f"[B站历史查看器] 端口 {port} 已被占用 —— 服务很可能已经在运行。", flush=True)
            print(f"                 直接打开 http://127.0.0.1:{port} 即可；", flush=True)
            print(f"                 若想重启，请先结束占用该端口的进程，再重新运行本命令。", flush=True)
        else:
            print(f"[B站历史查看器] 无法绑定端口 {port}：{e}", flush=True)
        sys.exit(1)
    print(f"[B站历史查看器] 已启动: http://127.0.0.1:{port}  (也可访问 http://localhost:{port})", flush=True)
    print(f"[B站历史查看器] 数据库: {get_db_path()}", flush=True)
    # 启动即打印当前规则状态（待应用 / 脏计数），作为"更新简要信息"
    try:
        st = rules_status()
        print(f"[B站历史查看器] 规则状态: 待应用(pending)={st.get('pending')}  自上次应用以来新增未套用(dirty_count)={st.get('dirty_count')}", flush=True)
    except Exception:
        pass
    print(f"[B站历史查看器] 按 Ctrl+C 停止（关闭本窗口也会停止服务）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
