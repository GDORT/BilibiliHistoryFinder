#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""续看规则引擎 —— 可移植纯逻辑模块（零依赖：仅标准库，不碰数据库/网络）。

抽取自 server.py 的规则引擎部分，作为 Phase 0「可移植纯逻辑」资产：
- 被本地 Web 服务端（server.py）、只读适配器（adapter_analyzer.py）、未来的油猴 D 路线复用；
- 输入为「canonical derived」记录字典，输出为续看分类（auto_skip / stale / None）与三视图计数；
- 不 import collector、不连接任何数据库，保证可独立单测与跨数据源复用。

字段约定（canonical derived，Analyzer 34 列为基准）：
- business / duration / author_name / author_mid / title / progress / view_at / archived_only
"""
import json
import re
import time
import hashlib

ALL_BUSINESS = ["archive", "pgc", "article", "live"]


# ===================== 规则定义与加载 =====================

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


def get_active_rule(rules):
    """返回当前 active 规则（同一时刻至多一条 active）。"""
    for r in rules.get("rules", []):
        if r.get("active"):
            return r
    return None


def load_rules_from_file(path):
    """从 rules.json 读取规则；不存在或损坏时回退默认规则。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("rules"), list):
            d = default_rules()
    except Exception:
        d = default_rules()
    if not any(r.get("active") for r in d.get("rules", [])):
        if d.get("rules"):
            d["rules"][0]["active"] = True
    return d


# ===================== 续看分类核心 =====================

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


def eval_condition(rec, cond, now, ctx=None):
    """单条件求值（高级筛选器超集）。新增算子：exists/empty/relative_after/relative_before/
    regex/not_contains/==/!=/in_list/not_in_list/not_between；字段解析扩展到 tag_name/remark/
    main_category/is_fav/live_status/videos/progress/view_at/dt。

    ctx（可选）：{ "lists": {列表名: set(值)} } 供 in_list/not_in_list 引用。
    """
    field = cond.get("field")
    op = cond.get("op")
    val = cond.get("value")
    der = _derived(rec, now)
    if field == "progress_pct":
        cur = der["progress_pct"]
    elif field == "progress_sec":
        cur = der["progress_sec"]
    elif field == "view_at_age_days":
        cur = der["view_at_age_days"]
    elif field == "business":
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
    elif field == "tag_name":
        cur = rec.get("tag_name") or ""
    elif field == "remark":
        cur = rec.get("remark") or ""
    elif field == "main_category":
        cur = rec.get("main_category") or ""
    elif field == "is_fav":
        cur = rec.get("is_fav")
    elif field == "live_status":
        cur = rec.get("live_status")
    elif field == "videos":
        cur = rec.get("videos")
    elif field == "progress":
        cur = rec.get("progress")
    elif field == "view_at":
        cur = rec.get("view_at")
    elif field == "dt":
        cur = rec.get("dt")
    else:
        cur = None

    # 空值判定（不依赖 now）
    if op == "exists":
        return cur not in (None, "")
    if op == "empty":
        return cur in (None, "")

    # 相对时间：相对 now 的天数窗（view_at 秒 或 view_at_age_days 天）
    if op in ("relative_after", "relative_before"):
        secs = _parse_duration(val)
        days = secs / 86400.0
        if field == "view_at":
            ref = rec.get("view_at")
            age_days = (now - ref) / 86400.0 if ref else None
        else:
            age_days = cur if isinstance(cur, (int, float)) else None
        if age_days is None:
            return False
        if op == "relative_after":   # 近 N 天内
            return age_days <= days
        return age_days > days        # relative_before：超过 N 天

    if op == "regex":
        try:
            return re.search(str(val), str(cur or "")) is not None
        except Exception:
            return False
    if op == "contains":
        return bool(cur) and str(val) in str(cur)
    if op == "not_contains":
        return not (bool(cur) and str(val) in str(cur))
    if op == "in_list":
        vals = (ctx or {}).get("lists", {}).get(str(val), set())
        return cur in vals
    if op == "not_in_list":
        vals = (ctx or {}).get("lists", {}).get(str(val), set())
        return cur not in vals

    if op in ("in", "not_in"):
        if isinstance(val, list):
            vals = [str(v).strip() for v in val if str(v).strip()]
        else:
            vals = [v.strip() for v in str(val).split(",") if v.strip()]
        inset = cur in vals if cur is not None else False
        return inset if op == "in" else (not inset)
    if op == "==":
        return cur == val
    if op == "!=":
        return cur != val
    if op == "between":
        try:
            a, b = [float(x) for x in str(val).split("-")]
            cv = float(cur)
            return a <= cv <= b
        except Exception:
            return False
    if op == "not_between":
        try:
            a, b = [float(x) for x in str(val).split("-")]
            cv = float(cur)
            return not (a <= cv <= b)
        except Exception:
            return False
    if cur is None:
        return False
    try:
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


def eval_group(rec, g, now, ctx=None):
    for c in g.get("conditions", []):
        if not eval_condition(rec, c, now, ctx):
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


# ===================== 三视图分类（只读，面向任意数据源） =====================

def classify(records, rule, now=None):
    """对一组 canonical 记录跑当前 active 规则，返回三视图计数与命中样本。

    视图语义（只读、不写库）：
    - finished：已看完（progress == -1）
    - auto_skip：被规则命中（action=='auto_skip'）
    - stale：被规则命中（action=='stale'）
    - needs：未完成 且 未被规则跳过（即「需要观看」）

    返回 dict：
      total / finished / auto_skip / stale / needs / active_rule_id
      以及 samples（每类最多若干条，含 kid/title/business/progress/duration/view_at/action/label）
    """
    if now is None:
        now = int(time.time())
    total = finished = auto_skip = stale = needs = 0
    samples = {"auto_skip": [], "stale": [], "needs": []}
    for rec in records:
        total += 1
        prog = rec.get("progress")
        if prog == -1:
            finished += 1
            continue
        res = evaluate_rule(rec, rule, now)
        if res:
            action, label = res
            if action == "stale":
                stale += 1
            else:
                auto_skip += 1
            # 已看完不会到这（evaluate_rule 已拦截），故 needs 不计入
            if len(samples[action if action == "stale" else "auto_skip"]) < 5:
                samples[action if action == "stale" else "auto_skip"].append({
                    "kid": rec.get("kid"), "bvid": rec.get("bvid"),
                    "title": rec.get("title"), "business": rec.get("business"),
                    "progress": prog, "duration": rec.get("duration"),
                    "view_at": rec.get("view_at"), "action": action, "label": label,
                })
        else:
            needs += 1
            if len(samples["needs"]) < 5:
                samples["needs"].append({
                    "kid": rec.get("kid"), "bvid": rec.get("bvid"),
                    "title": rec.get("title"), "business": rec.get("business"),
                    "progress": prog, "duration": rec.get("duration"),
                    "view_at": rec.get("view_at"),
                })
    return {
        "total": total,
        "finished": finished,
        "auto_skip": auto_skip,
        "stale": stale,
        "needs": needs,
        "active_rule_id": rule.get("id"),
        "samples": samples,
    }


def rules_hash(rules):
    cur = json.dumps(rules, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(cur.encode("utf-8")).hexdigest()


# ===================== 高级筛选器（当前引擎规则的超集） =====================
# 设计原则：分类核心（classify / evaluate_rule / 三视图不变式 / 跳过优先级 /
# 冲突校验 / 单 active 规则）一律保留。高级筛选器是「读时查询层」，作用于已分类记录，
# 只做子集/排序/聚合，不改变归属（auto_skip / manual_skip 等仍由规则引擎决定）。

def _parse_duration(val):
    """把 '7d'/'30d'/'24h'/'2w'/'3m' 或纯数字(天) 解析为秒。"""
    if val is None:
        return 0
    s = str(val).strip().lower()
    mult = {"d": 86400, "w": 604800, "h": 3600, "m": 2592000}
    if s and s[-1] in mult:
        try:
            return float(s[:-1]) * mult[s[-1]]
        except Exception:
            return 0
    try:
        return float(s) * 86400
    except Exception:
        return 0


def evaluate_filter(rec, spec, now, ctx=None):
    """对单条已分类记录跑高级筛选谓词，返回是否保留。

    spec = {
      "logic": "AND"|"OR"|"NOR",          # 顶层分组间布尔
      "negate": bool,                     # 整体取反
      "groups": [ { "logic": "AND"|"OR", "conditions": [cond...] } ],
      "having": {"groupBy": "author_mid", "op": ">", "value": 3} | null
    }
    ctx = { "lists": {列表名: set(值)}, "group_counts": {key: count} }
    """
    if not spec:
        return True
    groups = spec.get("groups") or []
    if not groups:
        result = True
    else:
        gr = [eval_group(rec, g, now, ctx) for g in groups]
        logic = (spec.get("logic") or "AND").upper()
        if logic == "OR":
            result = any(gr)
        elif logic == "NOR":
            result = not any(gr)
        else:
            result = all(gr)
    if spec.get("negate"):
        result = not result
    having = spec.get("having")
    if having and result:
        gb = having.get("groupBy")
        counts = (ctx or {}).get("group_counts") or {}
        key = rec.get(gb)
        cnt = counts.get(key, 0)
        try:
            v = float(having.get("value", 0))
        except Exception:
            v = 0
        op = having.get("op", ">")
        if op == ">":
            result = cnt > v
        elif op == ">=":
            result = cnt >= v
        elif op == "<":
            result = cnt < v
        elif op == "<=":
            result = cnt <= v
        elif op == "==":
            result = cnt == v
        else:
            result = False
    return result


def validate_filter(spec):
    """高级筛选器冲突校验（轻量）：同分组同字段既 ≥a 又 ≤b 且 a≥b → 永假（hard）。"""
    hard, soft = [], []
    if not spec:
        return {"hard": hard, "soft": soft}
    for gi, g in enumerate(spec.get("groups") or []):
        byfield = {}
        for c in g.get("conditions", []):
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
                        hard.append(f"筛选分组 #{gi+1} 同字段 {f} 既要求 ≥{a} 又要求 ≤{b}，"
                                    f"无记录可同时满足")
    return {"hard": hard, "soft": soft}


def sort_value(rec, field, now):
    """排序键：None 永远排末尾；(0, 数值|小写文本) 比较。"""
    der = _derived(rec, now)
    mapping = {
        "view_at": rec.get("view_at"),
        "duration": rec.get("duration"),
        "progress": rec.get("progress"),
        "progress_pct": der["progress_pct"],
        "progress_sec": der["progress_sec"],
        "view_at_age_days": der["view_at_age_days"],
        "title": rec.get("title") or "",
        "author_name": rec.get("author_name") or "",
        "business": rec.get("business") or "",
        "main_category": rec.get("main_category") or "",
    }
    v = mapping.get(field, rec.get(field))
    if v is None:
        return (1, 0)
    if isinstance(v, str):
        return (0, v.lower())
    try:
        return (0, float(v))
    except Exception:
        return (0, 0)


def apply_sort(recs, sort_fields, now):
    """稳定多列排序：从末列往首列依次排序，使首列为主序。dir='desc' 降序。"""
    if not sort_fields:
        return
    for sf in reversed(sort_fields):
        f = sf.get("field")
        rev = (sf.get("dir") or "desc") == "desc"
        recs.sort(key=lambda r, ff=f: sort_value(r, ff, now), reverse=rev)
