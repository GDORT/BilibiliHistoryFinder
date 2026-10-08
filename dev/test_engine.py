# -*- coding: utf-8 -*-
"""`engine.py` 单测 —— 续看规则引擎（**498 行，此前零覆盖**）。

为什么它是 `dev/README` §6.3 缺口清单的**第 1 优先**：

  `engine.py` 被显式设计成**纯逻辑模块** —— 零依赖、不碰库、不发网络（见
  `doc/现状.md` §2.2）。**这正是它最该有单测的理由：测它不需要任何外部条件。**

覆盖范围（16 个公开/半公开函数）：
  · 派生字段   `_derived`
  · 条件求值   `eval_condition`（含 15+ 算子）
  · 组/规则    `eval_group` · `evaluate_rule`
  · 分类       `classify`  ← 业务核心
  · 校验       `validate_rule`（hard/soft/info 三级冲突）
  · 规则装载   `default_rules` · `get_active_rule` · `load_rules_from_file` · `rules_hash`
  · 筛选器     `_parse_duration` · `evaluate_filter` · `validate_filter`
  · 排序       `sort_value` · `apply_sort`

⚠️ 本文件**零 IO、零网络**，只 import `engine` —— 与 `test_capabilities.py` 的
「默认零 IO」纪律一致，因此可以随时跑。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import engine  # noqa: E402

# ---------------------------------------------------------------- 断言helper

_OK = 0
_FAIL = 0
_FAILED = []


def check(cond, tag, detail=""):
    global _OK, _FAIL
    if cond:
        _OK += 1
    else:
        _FAIL += 1
        _FAILED.append("%s%s" % (tag, ("  | " + str(detail)) if detail else ""))
        print("  x FAIL %s%s" % (tag, ("  | " + str(detail)) if detail else ""))


def eq(got, want, tag):
    check(got == want, tag, "got=%r want=%r" % (got, want))


def sec(t):
    print("\n-- %s" % t)


# ---------------------------------------------------------------- 固定时间

NOW = 1_700_000_000          # 固定 now，避免测试随时钟漂移
DAY = 86400


def rec(**kw):
    """构造一条最小记录（只给用到的字段）。"""
    base = {"kid": "k1", "bvid": "BV1", "title": "t", "business": "archive",
            "view_at": NOW - 10 * DAY, "progress": 0, "duration": 0, "author_name": "a"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 1. 派生字段

def t1_derived():
    sec("E1 `_derived` —— 派生旋钮（progress_pct / progress_sec / view_at_age_days）")
    # progress == -1 → 视为看完
    d = engine._derived(rec(progress=-1, duration=100), NOW)
    eq(d["progress_pct"], 1.0, "progress=-1 → pct=1.0（看完）")
    eq(d["progress_sec"], 100, "progress=-1 → sec=duration（按整片算）")

    # 正常折算
    d = engine._derived(rec(progress=30, duration=120), NOW)
    eq(round(d["progress_pct"], 4), 0.25, "progress=30/duration=120 → pct=0.25")
    eq(d["progress_sec"], 30, "sec=progress 原值")

    # ⚠️ 除零防护：duration 为 0/None 时 pct 必须是 None（**不能崩，也不能给假值**）
    for dur in (0, None):
        d = engine._derived(rec(progress=0, duration=dur), NOW)
        eq(d["progress_pct"], None, "duration=%r → pct=None（**缺分母不给假值**）" % dur)

    # 缺 progress
    d = engine._derived(rec(progress=None, duration=100), NOW)
    eq(d["progress_pct"], None, "progress=None → pct=None")

    # view_at_age_days
    d = engine._derived(rec(view_at=NOW - 7 * DAY), NOW)
    eq(round(d["view_at_age_days"], 4), 7.0, "view_at 距今 7 天 → age=7")
    eq(engine._derived(rec(view_at=0), NOW)["view_at_age_days"], None,
       "view_at=0（缺失）→ age=None")


# ---------------------------------------------------------------- 2. 条件求值

def t2_eval_condition():
    sec("E2 `eval_condition` —— 算子全集")
    r = rec(progress=30, duration=120, business="archive", view_at=NOW - 100 * DAY,
            title="Rust 入门", author_name="alice", tag_name="编程",
            remark="互动补充：xx", main_category="科技", is_fav=1, videos=3, dt="web")

    def ev(field, op, value):
        return engine.eval_condition(r, {"field": field, "op": op, "value": value}, NOW)

    eq(ev("progress_pct", ">=", 0.2), True, "pct >= 0.2")
    eq(ev("progress_pct", "<", 0.2), False, "pct < 0.2（不满足）")
    eq(ev("progress", "==", 30), True, "== ")
    eq(ev("progress", "!=", 30), False, "!= ")
    eq(ev("business", "in", ["archive", "live"]), True, "in 列表")
    eq(ev("business", "not_in", ["archive", "live"]), False, "not_in 列表")
    # ⚠️ `between` 的 value 是**连字符字符串**（`"10-40"`），不是 list（实测 L190）
    eq(ev("progress", "between", "10-40"), True, "between 区间内（'10-40'）")
    eq(ev("progress", "between", "50-90"), False, "between 区间外")
    eq(ev("progress", "not_between", "10-40"), False, "not_between")
    eq(ev("progress", "between", "坏格式"), False, "between 坏格式 → False（不崩）")
    eq(ev("view_at_age_days", ">", 90), True, "view_at_age_days > 90")
    eq(ev("title", "contains", "入门"), True, "contains")
    eq(ev("title", "not_contains", "入门"), False, "not_contains")
    eq(ev("title", "regex", r"^Rust"), True, "regex")
    eq(ev("is_fav", "exists", None), True, "exists（有值）")
    eq(ev("remark", "empty", None), False, "empty（有备注）")
    # ⚠️ `relative_after` 语义是「**近 N 天内**」（age_days <= N），不是「N 天前之后」
    #    且 op 不比较大小 —— 直接 `relative_after, "1d"`。
    eq(ev("view_at", "relative_after", "200d"), True, "relative_after 200d（100 天前 → 在近 200 天内）")
    eq(ev("view_at", "relative_after", "10d"), False, "relative_after 10d（100 天前 → 不在近 10 天内）")
    eq(ev("view_at", "relative_before", "10d"), True, "relative_before 10d（超过 10 天 → 满足）")
    eq(ev("view_at", "relative_after", None), False, "view_at 缺失 → False（不崩）")

    # ⚠️ 关键：字段不存在时**不得抛异常**，也不得静默判 True
    eq(ev("nonexistent_field", ">", 1), False, "**不存在的字段 → False**（不崩、不误判）")
    # 空值字段
    eq(ev("tag_name", "contains", "x"), False, "空字段 contains → False")


# ---------------------------------------------------------------- 3. 组与规则

def t3_group_rule():
    sec("E3 `eval_group` / `evaluate_rule` —— match any/all 与组内 NOT")
    cond = lambda f, o, v: {"field": f, "op": o, "value": v}  # noqa: E731
    r = rec(progress=95, duration=100)

    # ⚠️ `eval_group` 是**组内全满足（AND）**语义（实测 L222-227：任一不满足即 False）；
    #    `any` / `all` 的区分在 **`evaluate_rule`** 层（按 groups 命中数）。
    g_any = {"conditions": [cond("progress_pct", "<", 0.1), cond("progress_pct", ">=", 0.9)]}
    eq(engine.eval_group(r, g_any, NOW, None), False, "组内两个条件只满足一个 → False（AND 语义）")

    # all：必须全部满足
    g_all = {"match": "all", "conditions": [cond("progress_pct", ">=", 0.9),
                                            cond("duration", ">", 50)]}
    eq(engine.eval_group(r, g_all, NOW, None), True, "all 且都满足 → 真")
    g_all2 = {"match": "all", "conditions": [cond("progress_pct", ">=", 0.9),
                                              cond("duration", "<", 50)]}
    eq(engine.eval_group(r, g_all2, NOW, None), False, "all 但有一个不满足 → 假")

    # 空组：实现是「循环不执行 → return True」（AND 的空真），**照实断言，不臆造**
    eq(engine.eval_group(r, {"conditions": []}, NOW, None), True, "空组 → True（AND 空真，照实现）")

    # any / all 的真正区别在 evaluate_rule
    any_rule = {"id": "a", "active": True, "match": "any", "groups": [
        {"label": "A", "action": "auto_skip", "conditions": [cond("progress_pct", "<", 0.1)]},
        {"label": "B", "action": "auto_skip", "conditions": [cond("progress_pct", ">=", 0.9)]}]}
    eq(engine.evaluate_rule(r, any_rule, NOW), ("auto_skip", "B"),
       "match=any：命中任一组即返回**第一个命中组**")
    all_rule = dict(any_rule, match="all")
    eq(engine.evaluate_rule(r, all_rule, NOW), None,
       "match=all：未全部命中 → None")

    # evaluate_rule：多组 any
    rule = {"id": "t", "active": True, "match": "any", "groups": [
        {"label": "A", "action": "auto_skip", "conditions": [cond("progress_pct", "<", 0.1)]},
        {"label": "B", "action": "stale", "conditions": [cond("view_at_age_days", ">", 90)]},
    ]}
    # ⚠️ `evaluate_rule` 返回 **(action, label) 元组** 或 None（实测 L229），不是 dict
    hit = engine.evaluate_rule(r, rule, NOW)
    check(hit is None or (isinstance(hit, tuple) and len(hit) == 2),
          "evaluate_rule 返回 (action,label) 或 None", "got=%r" % (hit,))


# ---------------------------------------------------------------- 4. 分类（核心）

def t4_classify():
    sec("E4 `classify` —— **业务核心**：五分类 ＋ 跳过优先级")
    rules = engine.default_rules()
    rule = rules["rules"][0]

    def cls(**kw):
        """`classify` 返回**计数字典**（total/finished/auto_skip/stale/needs/samples），
        命中的明细在 `samples['auto_skip']` / `['stale']` 里（实测 L295-352）。"""
        out = engine.classify([rec(**kw)], rule, now=NOW)
        # ⚠️ stale 的前缀是 `stale::`（不是 auto_skip::）—— 与库里 auto_skip_reason 的写法一致
        for bucket, prefix in (("auto_skip", "auto_skip::"), ("stale", "stale::")):
            if out.get(bucket):
                s0 = out["samples"][bucket][0]
                return {"auto_skip": 1, "auto_skip_reason": prefix + s0["label"]}
        return {"auto_skip": 0, "auto_skip_reason": None}

    # 接近看完
    c = cls(progress=98, duration=100)
    eq(c.get("auto_skip_reason"), "auto_skip::接近看完", "progress 98% → 接近看完")

    # 短视频碎片（duration<60）
    c = cls(progress=10, duration=45)
    eq(c.get("auto_skip_reason"), "auto_skip::短视频碎片", "duration=45s → 短视频碎片")

    # 直播回放 —— ⚠️ 照实：**必须给 duration>60 才会命中**，否则先被「短视频碎片」抢走
    #   （默认规则里碎片组排在直播组之前；`duration=0` 满足 `<60`）。
    # progress 要给 >0 的值，否则 pct=0 且 sec<15 → 先命中「误触-短暂观看」（它排在直播组之前）
    c = cls(business="live", progress=300, duration=900)
    eq(c.get("auto_skip_reason"), "auto_skip::直播回放",
       "business=live ＋ duration=900 ＋ progress>0 → 直播回放")
    c_live0 = cls(business="live", progress=0, duration=0)
    # ✅ `#41` 修好后的**正确**行为：`duration=0` 不再参与时长类条件 → 不再被「短视频碎片」抢走，
    #    直播**正确落到**「直播回放」组（`business in ['live']` 不依赖时长）。
    #    修前这里命中的是「短视频碎片」—— 那正是 #41 的误伤形态。
    eq(c_live0.get("auto_skip_reason"), "auto_skip::直播回放",
       "**#41 修好后：缺时长的直播落到「直播回放」**（不再被碎片抢走）")

    # 陈旧（>90 天未看，且未被上面命中）
    # ⚠️ 陈旧组在默认规则里是**最后一个**，而前面三组（接近看完/误触/碎片）优先 ——
    #   `duration=1000`（>60，不命中碎片）＋ `progress=0`（pct=0，命中「误触」的 pct<0.03 但
    #   progress_sec=0<15 也满足 → **会先命中「误触-短暂观看」**）。故造一条 progress=50 的。
    c = cls(view_at=NOW - 200 * DAY, progress=50, duration=1000)
    # ⚠️ 照实：`cls()` 助手按 bucket 拼前缀（`stale::` / `auto_skip::`），
    #   而 `evaluate_rule` 返回的 action 确实是 `stale`（无 `stale::` 前缀）
    eq(c.get("auto_skip_reason"), "stale::陈旧-久未观看", "200 天未看 ＋ 无其他命中 → 陈旧")

    # 都没命中
    c = cls(progress=50, duration=300, view_at=NOW - DAY)
    eq(c.get("auto_skip"), 0, "普通视频 → 不跳过")

    # ✅ `#41` **已修**（2026-10-05，方案 B：只在「缺时长数据」时短路求值）。
    #   修前：`duration=0` 满足 `0 < 60` → 462 条长尾里 435 条（94.2%）会被误标「短视频碎片」；
    #   修后：**缺时长不参与时长类条件**，而**有真实时长时行为完全不变**。
    c = cls(progress=0, duration=0)
    eq(c.get("auto_skip_reason"), None, "**缺 duration 不再被误判为「短视频碎片」**（#41 已修）")
    eq(cls(progress=0, duration=None).get("auto_skip_reason"), None,
       "duration=None 同样不参与时长类条件")
    # 真实短视频**仍要命中**（别把规则本意一起修掉）
    c2 = cls(progress=5, duration=30)
    eq(c2.get("auto_skip_reason"), "auto_skip::短视频碎片",
       "真实 30 秒视频仍正确命中「短视频碎片」（规则本意未受损）")
    # 守卫刻意**不拦** exists / empty（那俩算子的语义就是查空值本身）
    eq(engine.eval_condition(rec(duration=0), {"field": "duration", "op": "exists", "value": None}, NOW),
       True, "守卫不拦 `exists`（duration=0 算「有值」）")
    eq(engine.eval_condition(rec(duration=None), {"field": "duration", "op": "empty", "value": None}, NOW),
       True, "守卫不拦 `empty`（duration=None 算「空」）")

    # ---- C2（2026-10-06 评估稿 7-a）：`#41` 守卫对**反向算子**的边界语义 ----
    #
    # ⚠️ **这是一个已知的语义取舍，不是 bug** —— 写清楚以免日后误改：
    #   守卫只问「`duration` 有没有值」，**不区分正向/反向算子**。于是缺时长时
    #   `!=` / `not_contains` / `not_in_list` / `not_between` 一律返 `False`，
    #   意味着「时长 ≠ 60」这类规则会**把缺时长的记录排除**（而不是保留）。
    #   当前默认规则**只用 `<`**（时长类），故**现状无误伤**；一旦日后写反向规则
    #   就会踩到这个边界 —— 那时需要的是「显式选择语义」，而不是悄悄绕过守卫。
    print("  -- 边界：缺 duration 时**所有**算子（含反向）一律 False --")
    rev_ops = [
        ("!=", 60, "!= 60"),
        ("not_contains", "x", "not_contains 'x'"),
        ("not_in_list", [1, 2], "not_in_list [1,2]"),
        ("not_between", "60-120", "not_between 60-120"),
    ]
    for op, val, label in rev_ops:
        eq(engine.eval_condition(rec(duration=0), {"field": "duration", "op": op, "value": val}, NOW),
           False, "缺 duration → `%s` 返 False（**已知取舍**：反向规则会排除缺时长记录）" % label)
        eq(engine.eval_condition(rec(duration=None), {"field": "duration", "op": op, "value": val}, NOW),
           False, "duration=None → `%s` 同样返 False" % label)

    # 有真实值时反向算子**必须正常参与**（否则守卫就把规则写死了）
    for op, val, label in rev_ops:
        got = engine.eval_condition(rec(duration=45), {"field": "duration", "op": op, "value": val}, NOW)
        eq(got, True, "**有真实值时 `%s` 照常求值**（守卫只拦缺数据，不改规则语义）" % label)

    # 正向算子对照组（缺时长一律 False —— 这是 `#41` 的**本意**）
    for op, val, label in [("<", 60, "< 60"), ("==", 60, "== 60"),
                           (">", 60, "> 60"), ("in_list", [60], "in_list [60]")]:
        eq(engine.eval_condition(rec(duration=0), {"field": "duration", "op": op, "value": val}, NOW),
           False, "缺 duration → `%s` 返 False（#41 本意：缺时长不参与时长类条件）" % label)

    # 边界值：duration=1（1 秒的极短视频）**必须正常参与**，不能被当缺数据
    eq(engine.eval_condition(rec(duration=1), {"field": "duration", "op": "<", "value": 60}, NOW),
       True, "**duration=1 照常参与**（守卫判的是 `≤0`，不是「小于某阈值」）")

    # 脏数据：非数值字符串会**在 `_derived` 里就崩**（`dur > 0` 对 str 抛 TypeError），
    # ⇒ `#41` 守卫的 `except` 分支**够不到**它。
    # ⚠️ **这是新发现的防御性缺口（2026-10-06）**，不是 `#41` 引入的：
    #   `_derived` L81 `elif prog is not None and dur and dur > 0` 对非数值类型直接抛。
    #   **实测真实库里 `duration` 非整数的行为 0 条**（本地库与 Analyzer 年表都核过）
    #   → 现场无风险，**不改 `_derived`**（那属分类语义变更，需拍板），只照实断言 ＋ 标注。
    def _derived_crash(**kw):
        try:
            engine._derived(rec(**kw), NOW)
            return None
        except TypeError as e:
            return "TypeError: %s" % str(e)[:40]

    got = _derived_crash(duration="abc")
    check(got is not None and "TypeError" in got,
          "【已知缺口·照实】`_derived` 遇非数值 duration **抛 TypeError**（守卫够不到）",
          "实际=%r" % got)
    # ⭐ 但 `field != duration` 的字段不受影响（守卫不得越权）
    eq(engine.eval_condition(rec(progress=0, duration=None), {"field": "progress", "op": "<", "value": 15}, NOW),
       True, "守卫**只作用于 duration 字段**（progress 缺别的照常求值）")

    # 批量分类
    out = engine.classify([rec(progress=98, duration=100), rec(progress=0, duration=10)],
                          rule, now=NOW)
    eq(out["total"], 2, "批量分类 total=2")
    eq(out["auto_skip"], 2, "两条都被跳过")
    labels = sorted(s["label"] for s in out["samples"]["auto_skip"])
    # ⚠️ 照实：第二条 progress=0/duration=10 → pct=0.0 <0.03 且 sec=0<15
    #   **同时满足「误触-短暂观看」**；而该组排在碎片组之前 → 命中「误触」。
    eq(labels, sorted(["接近看完", "误触-短暂观看"]),
       "批量里各条独立判定（第二条命中的是**误触**而非碎片 —— 组顺序决定）")

    # 计数不变量：finished ＋ auto_skip ＋ stale ＋ needs == total
    recs = [rec(progress=-1, duration=100), rec(progress=98, duration=100),
            rec(view_at=NOW - 200 * DAY, progress=50, duration=1000), rec(progress=50, duration=300)]
    o = engine.classify(recs, rule, now=NOW)
    eq(o["finished"] + o["auto_skip"] + o["stale"] + o["needs"], o["total"],
       "**不变量**：finished＋auto_skip＋stale＋needs == total")
    eq(o["finished"], 1, "progress=-1 → finished")
    eq(o["stale"], 1, "200 天未看 → stale")


# ---------------------------------------------------------------- 5. 规则装载与校验

def t5_rules():
    sec("E5 规则装载 / 冲突校验 / 哈希")
    rules = engine.default_rules()
    eq(len(rules["rules"]), 1, "默认规则 1 条")
    eq(rules["rules"][0]["active"], True, "默认规则是 active")
    labels = [g["label"] for g in rules["rules"][0]["groups"]]
    for want in ("接近看完", "误触-短暂观看", "短视频碎片", "直播回放", "陈旧-久未观看"):
        check(want in labels, "默认规则含「%s」" % want, "实际=%s" % labels)

    # get_active_rule：至多一条 active
    eq(engine.get_active_rule(rules)["id"], "default", "get_active_rule 返回 active 那条")
    two = {"rules": [{"id": "a", "active": True}, {"id": "b", "active": True}]}
    eq(engine.get_active_rule(two)["id"], "a", "两条 active 时取第一条（不崩）")
    eq(engine.get_active_rule({"rules": [{"id": "a", "active": False}]}), None,
       "无 active → 返回 None")

    # rules_hash：与内容绑定、且对键序不敏感
    h1 = engine.rules_hash(rules)
    shuffled = {"rules": list(reversed(rules["rules"]))}
    eq(engine.rules_hash(shuffled), h1, "rules_hash 对规则顺序不敏感（sort_keys）")
    changed = json.loads(json.dumps(rules))
    changed["rules"][0]["groups"][0]["conditions"][0]["value"] = 0.5
    check(engine.rules_hash(changed) != h1, "改内容 → hash 变（能测出「改了规则没应用」）")

    # load_rules_from_file
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False)
        path = f.name
    try:
        eq(len(engine.load_rules_from_file(path)["rules"]), 1, "从文件装载默认规则")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ 坏 json ")
        got = engine.load_rules_from_file(path)
        check("rules" in got, "**坏文件不崩** → 回退到默认规则", "got=%r keys=%s" % (
            type(got).__name__, sorted(got.keys()) if isinstance(got, dict) else "?"))
    finally:
        os.unlink(path)
    eq(len(engine.load_rules_from_file("不存在的路径.json")["rules"]), 1, "文件不存在 → 默认规则")

    # validate_rule：hard 冲突（同一字段 >= 与 <= 交叉）
    bad = {"id": "x", "active": True, "match": "all", "groups": [
        {"label": "G", "action": "auto_skip", "conditions": [
            {"field": "progress_pct", "op": ">=", "value": 0.9},
            {"field": "progress_pct", "op": "<=", "value": 0.1}]}]}
    v = engine.validate_rule(bad)
    check(v.get("hard"), "同组 >=0.9 与 <=0.1 → **hard 冲突**", "v=%r" % v)
    ok = engine.default_rules()["rules"][0]
    v2 = engine.validate_rule(ok)
    check(not v2.get("hard"), "默认规则无 hard 冲突", "v=%r" % v2)


# ---------------------------------------------------------------- 6. 高级筛选器

def t6_filter():
    sec("E6 `evaluate_filter` / `validate_filter` —— 高级筛选器（规则的超集）")
    r = rec(progress=30, duration=120, view_at=NOW - 5 * DAY, title="Rust 入门",
            business="archive", author_name="alice")

    eq(engine.evaluate_filter(r, {"field": "progress_pct", "op": ">=", "value": 0.2}, NOW), True,
       "filter >= 0.2")
    eq(engine.evaluate_filter(r, {"field": "title", "op": "contains", "value": "入门"}, NOW), True,
       "filter contains")

    # duration 字符串解析
    eq(engine._parse_duration("7d"), 7 * DAY, "7d → 7 天秒数")
    eq(engine._parse_duration("24h"), DAY, "24h → 1 天")
    eq(engine._parse_duration("2w"), 14 * DAY, "2w → 14 天")
    eq(engine._parse_duration("30"), 30 * DAY, "纯数字按天")
    # ⚠️ 照实：`_parse_duration(None)` 返回 **0**（不是 None）—— `relative_*` 里
    #   「0 天」意为「近 0 天内」，缺值时不会被当成宽泛命中。
    eq(engine._parse_duration(None), 0, "None → 0（照实；非 None）")

    # 空 filter → 全通过（不筛）
    eq(engine.evaluate_filter(r, {}, NOW), True, "空 filter → True")

    # validate_filter：坏算子应被拒
    bad = engine.validate_filter({"field": "title", "op": "不存在的算子", "value": "x"})
    check(bad is not None, "坏算子被 validate_filter 拦下", "got=%r" % (bad,))
    # ⚠️ 照实：`validate_filter` 返回 **`{'hard':[], 'soft':[]}`**（空列表＝通过），
    #    与 `validate_rule` 的返回形状不同。
    good = engine.validate_filter({"field": "title", "op": "contains", "value": "x"})
    check(isinstance(good, dict) and not good.get("hard"),
          "合法 filter → {'hard':[],'soft':[]}（空 hard ＝ 通过）", "got=%r" % (good,))


# ---------------------------------------------------------------- 7. 排序

def t7_sort():
    sec("E7 `sort_value` / `apply_sort` —— **原地排序**、None 恒排末尾")
    def mk():
        return [
            rec(kid="a", view_at=NOW - 3 * DAY, duration=300, progress=30, title="BBB"),
            rec(kid="b", view_at=NOW - 1 * DAY, duration=100, progress=90, title="AAA"),
            rec(kid="c", view_at=None, duration=None, progress=None, title="CCC"),
        ]

    # ⚠️ 照实：**`apply_sort` 是 `recs.sort()` 原地排序、返回 `None`**（实测 L491-498），
    #   且只有 `if not sort_fields: return` 早退也是 None。调用方必须**看传入的那个 list**。
    rs = mk()
    eq(engine.apply_sort(rs, [{"field": "view_at", "dir": "desc"}], NOW), None,
       "apply_sort 返回 None（**原地排序**，不返回新 list）")
    # ✅ `#42` **已修**（2026-10-05）：`apply_sort` 改为**分段** ——
    #   有值段按方向排、缺值段**永远追加末尾**。`sort_value` 的返回形状**未动**（避免波及其它调用方）。
    #   修前：`['a','b','c']` 降序后变 `['c','b','a']`（缺值跑到最前），与 docstring 矛盾。
    eq([x["kid"] for x in rs], ["b", "a", "c"], "**降序时缺值仍在末尾**（#42 已修）")

    # 稳定性：同值元素保持原相对顺序
    rs_st = mk()
    rs_st[0]["view_at"] = rs_st[1]["view_at"]
    engine.apply_sort(rs_st, [{"field": "view_at", "dir": "desc"}], NOW)
    eq([x["kid"] for x in rs_st][:2], ["a", "b"], "降序时同值元素**保持原相对顺序**（稳定排序）")

    rs2 = mk()
    engine.apply_sort(rs2, [{"field": "title", "dir": "asc"}], NOW)
    eq([x["kid"] for x in rs2][:2], ["b", "a"], "按 title 升序（AAA 在 BBB 前）")
    eq(rs2[-1]["kid"], "c", "**升序时缺值在末尾**（符合 docstring）")

    # 缺值排在末尾时，真实值的相对顺序仍应是「新的在前」
    rs2b = mk()
    engine.apply_sort(rs2b, [{"field": "view_at", "dir": "asc"}], NOW)
    eq([x["kid"] for x in rs2b][:2], ["a", "b"], "升序时真实值按旧→新（对照上条）")

    # 未知字段：mapping.get 回退到 rec.get(field) → None → (1,0) → 排末尾，不崩
    rs3 = mk()
    engine.apply_sort(rs3, [{"field": "不存在的字段", "dir": "asc"}], NOW)
    eq(len(rs3), 3, "未知排序字段 → 全部当缺值处理（**不崩、不丢条**）")

    # 派生字段可作排序键
    rs4 = mk()
    engine.apply_sort(rs4, [{"field": "progress_pct", "dir": "asc"}], NOW)
    # 升序时 c（pct 缺）应在末尾，前两个按 pct：b(0.9) > a(0.1)
    eq([x["kid"] for x in rs4], ["a", "b", "c"], "progress_pct 升序：a(0.1)→b(0.9)→c(缺)")

    # sort_value 直接调用
    eq(engine.sort_value(rec(progress=30, duration=120), "progress_pct", NOW), (0, 0.25),
       "sort_value 返回 (0, 数值)")
    eq(engine.sort_value(rec(view_at=None), "view_at", NOW), (1, 0),
       "缺值 → (1,0) —— **1 段保证恒排末尾**")
    eq(engine.sort_value(rec(title="ABC"), "title", NOW), (0, "abc"),
       "字符串 → 小写比较（大小写不敏感）")

    # 多列排序：首列为主序
    rs5 = mk()
    engine.apply_sort(rs5, [{"field": "progress_pct", "dir": "asc"},
                            {"field": "view_at", "dir": "asc"}], NOW)
    eq(rs5[0]["kid"], "a", "多列排序：首列为主序（a 的 pct 最低）")
    eq(rs5[-1]["kid"], "c", "多列排序：缺值项仍在末尾")


# ---------------------------------------------------------------- main

def main():
    for fn in (t1_derived, t2_eval_condition, t3_group_rule, t4_classify,
               t5_rules, t6_filter, t7_sort):
        fn()
    print("\n" + "=" * 60)
    if _FAIL:
        print("  结果：%d 通过 / %d 失败" % (_OK, _FAIL))
        for f in _FAILED:
            print("    x %s" % f)
        return 1
    print("  结果：全部通过（%d 项断言）" % _OK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
