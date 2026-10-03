#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「连接即模式」阶段 1 的纯函数单测 —— 零 IO、不联网、不碰任何真实历史库。

为什么需要它（见 doc/log/2026-10-01-说明-待定项与流程影响.md §7）：
    `src/` 与 `dev/` 里此前**没有任何断言式单测**（`unittest` / `pytest` / `assert` 全部零命中），
    现有 4 个脚本全是"起真服务 / 起假后端 + 打印对比"的**集成级**装置。而阶段 1 新增的
    `derive_capabilities()` / `decide_sync_plan()` / `normalize_policy()` **恰是纯函数** ——
    设计时把 `probe` / `policy` / `state` 都做成入参，正是为这一层留的口子：
    **不需要起服务、不需要 mock 后端、不碰磁盘、毫秒级。**

它同时是阶段 2 的保险：阶段 2 要把抓取判据从"读手切模式"换成"读探测 + 策略"，
届时这套单测几秒内就能告诉你**决策逻辑有没有被改坏**（端到端要起服务、30 秒以上）。

安全性（本脚本的硬约束）：
    - 默认**不联网**：只 import `store` 与 `collector`（两者模块顶层都不打开任何库、不发任何请求）。
    - 唯一触盘的是 T5 / T6 —— 全部在 `tempfile.mkdtemp()` 里建**临时** sqlite / json，结束即删。
    - **不读不写** `data/` 下任何文件、**不动** 8765 服务、**不碰** Analyzer 库。
    - `--live` 是唯一会发请求的开关，且只打本机 8765 的**只读** GET，并带 `?sessdata=0`
      跳过凭证探测（连 B站都不打）。不带 `--live` 时一个请求都不发。

用法：
    python dev/test_capabilities.py            # 纯函数单测（默认，零 IO）
    python dev/test_capabilities.py --live     # 额外打一次本机 8765 /api/capabilities 做结构冒烟
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import store  # noqa: E402  （模块顶层无 IO，见 docstring）

NOW = 1758800000  # 固定"现在"，让所有断言与时间无关（可重复）

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


def sec(title):
    print("\n-- %s" % title)


# ---------------------------------------------------------------- 工厂

def mk_probe(reachable=True, fsess="present", a_days=None, l_days=None,
             a_count=0, l_count=0, merged=0, effective=None, diag=None, ok=None):
    """构造一个 probe（形状与 server.probe_connection() 的产出一致）。

    `ok` = **业务级可用**（`/health` HTTP 成功 **且** 可达）—— 能力层 / 策略层 /
    409 门控的**唯一判据**（2026-10-03 审查 ②）。缺省 = `reachable`（传输级），
    显式传 `ok=False, reachable=True` 即模拟「连得上但业务 502」。
    """
    ok = reachable if ok is None else ok
    return {
        "connection": {
            "analyzer": {"reachable": reachable, "ok": ok, "base": "http://localhost:8899",
                         "health_status": "running" if reachable else None,
                         "db_count": a_count,
                         "diagnosis": diag or {"level": "ok", "code": "ok"}},
            "finder": {"state": fsess, "owner": "finder(config.json)",
                       "sessdata": fsess, "db_count": l_count, "last_success_at": None},
        },
        "data": {"analyzer": a_count, "local": l_count, "merged": merged,
                 "effective": effective,
                 "span": {"analyzer_days": a_days, "local_days": l_days,
                          "gap_days": (a_days - l_days)
                          if (a_days is not None and l_days is not None) else None,
                          "analyzer_oldest": None, "local_oldest": None,
                          "peak_days": None}},
        "sessdata": {"owner": "Analyzer(config.yaml)", "state": "ok"},
        "source": {},
    }


# ---------------------------------------------------------------- T1 策略配置归一

def t1_normalize_policy():
    sec("T1 normalize_policy —— 旧 `mode` 三态 → 新 `prefer` 二态（兼容性）")

    p = store.normalize_policy({"mode": "local"})
    eq(p["prefer"], "local", "旧 mode=local → prefer=local")
    p = store.normalize_policy({"mode": "analyzer"})
    eq(p["prefer"], "auto", "旧 mode=analyzer → prefer=auto（冗余分支删除，D1）")
    p = store.normalize_policy({"mode": "auto"})
    eq(p["prefer"], "auto", "旧 mode=auto → prefer=auto")
    p = store.normalize_policy({})
    eq(p["prefer"], "auto", "空配置 → 默认 prefer=auto")
    eq(p["sync"], "auto", "空配置 → 默认 sync=auto")
    eq(sorted(p["rules"]), sorted(store.POLICY_DEFAULT["rules"]), "空配置 → rules 递归补齐")

    # 新格式优先，且 rules 只覆盖给到的键
    p = store.normalize_policy({"policy": {"prefer": "local", "sync": "full",
                                          "rules": {"full_cooldown_min": 5}}})
    eq(p["prefer"], "local", "新格式：prefer 生效")
    eq(p["sync"], "full", "新格式：sync 生效")
    eq(p["rules"]["full_cooldown_min"], 5, "新格式：rules 覆盖生效")
    eq(p["rules"]["full_interval_days"], 7, "新格式：未给的 rules 键回落默认")

    # 非法值静默回落（配置是人手改的，不该因一个错字而起不来）
    eq(store.normalize_policy({"policy": {"prefer": "bogus"}})["prefer"], "auto",
       "非法 prefer → 回落 auto")
    eq(store.normalize_policy({"policy": {"sync": "bogus"}})["sync"], "auto",
       "非法 sync → 回落 auto")
    eq(store.normalize_policy({"policy": {"rules": {"full_cooldown_min": "x"}}})["rules"]["full_cooldown_min"],
       10, "非数字 rules 值 → 回落默认")

    # 非 dict 入参不炸
    eq(store.normalize_policy(None)["prefer"], "auto", "None 入参 → 默认")
    eq(store.normalize_policy("junk")["prefer"], "auto", "字符串入参 → 默认")

    # 扩展位：未知 rules 键原样保留（Q1「后续增加具体策略」的落点）
    p = store.normalize_policy({"policy": {"rules": {"my_future_rule": 42}}})
    eq(p["rules"].get("my_future_rule"), 42, "rules 预留扩展位：未知键保留")


# ---------------------------------------------------------------- T2 能力层

def t2_derive_capabilities():
    sec("T2 derive_capabilities —— 唯一的可用性判断处（3 态）")

    # 组合形态：Analyzer 可达
    c = store.derive_capabilities(mk_probe(reachable=True))
    eq(tuple(c.keys()), store.CAPABILITY_KEYS, "键集合与 CAPABILITY_KEYS 一致（顺序也一致）")
    for k in store.CAPABILITY_KEYS:
        check(c[k]["available"] is True, "组合形态：%s 可用" % k, c[k])
        check(c[k]["reason"] == "", "组合形态：%s reason 为空" % k, c[k])
    eq(c["fetch"]["owner"], "analyzer", "组合形态：fetch owner=analyzer")
    eq(c["sync"]["owner"], "finder", "组合形态：sync owner=finder（本地刷新，恒可做）")
    eq(c["export"]["formats"], ["excel", "db"], "组合形态：export formats=[excel,db]")

    # 独立形态 + 凭证已填写 → 抓取仍可用（由本地 collector 执行）
    c = store.derive_capabilities(mk_probe(reachable=False, fsess="present"))
    check(c["fetch"]["available"] is True, "独立+present：fetch 可用")
    eq(c["fetch"]["owner"], "finder", "独立+present：fetch owner=finder")
    for k in ("remark", "export", "images", "integrity"):
        check(c[k]["available"] is False, "独立形态：%s 不可用" % k, c[k])
        check(c[k]["owner"] is None, "独立形态：%s owner=None" % k, c[k])
        check("Analyzer" in c[k]["reason"], "独立形态：%s reason 指向 Analyzer" % k, c[k])
    eq(c["export"]["formats"], [], "独立形态：export formats 为空")
    check(c["sync"]["available"] is True, "独立形态：sync 恒可用（纯本地刷新）")
    check(c["backup"]["available"] is True, "独立形态：backup 恒可用（本地快照）")

    # 独立形态 + 凭证缺失 → 抓取被挡（而不是"点了然后失败"）
    c = store.derive_capabilities(mk_probe(reachable=False, fsess="missing"))
    check(c["fetch"]["available"] is False, "独立+missing：fetch 不可用")
    check("SESSDATA" in c["fetch"]["reason"], "独立+missing：reason 指向 SESSDATA", c["fetch"])
    check(c["backup"]["available"] is True, "独立+missing：backup 仍可用")

    # 阶段 1.5 ④：值域扩为 5 值，valid 与 present 同视为可用，invalid 明确不可用
    c = store.derive_capabilities(mk_probe(reachable=False, fsess="valid"))
    check(c["fetch"]["available"] is True, "独立+valid：fetch 可用（联网验证通过）")
    eq(c["fetch"]["owner"], "finder", "独立+valid：fetch owner=finder")
    c = store.derive_capabilities(mk_probe(reachable=False, fsess="invalid"))
    check(c["fetch"]["available"] is False, "独立+invalid：fetch 不可用")
    check("SESSDATA" in c["fetch"]["reason"], "独立+invalid：reason 指向 SESSDATA", c["fetch"])
    check(c["backup"]["available"] is True, "独立+invalid：backup 仍可用")

    # unknown（探测不出结论）走保守分支 —— 不让用户点一个必然失败的抓取
    c = store.derive_capabilities(mk_probe(reachable=False, fsess="unknown"))
    check(c["fetch"]["available"] is False, "独立+unknown：fetch 保守不可用（unknown ≠ present）")
    check("未知" in c["fetch"]["reason"], "独立+unknown：reason 说明无法确认", c["fetch"])
    c = store.derive_capabilities(mk_probe(reachable=False, fsess=None))
    check(c["fetch"]["available"] is False, "sessdata 缺失（None）→ 同样保守")

    # 契约：每项都有三个键
    for k in store.CAPABILITY_KEYS:
        for key in ("available", "owner", "reason"):
            check(key in c[k], "能力项 %s 含键 %s" % (k, key), c[k])

    # 空 / 脏入参不炸
    check(store.derive_capabilities({})["fetch"]["available"] is False, "空 probe → fetch 不可用")
    check(store.derive_capabilities(None)["sync"]["available"] is True, "None probe → 不炸")

    # ⭐ 判据统一（2026-10-03 审查 ②）：`ok=False, reachable=True` 模拟「连得上但业务 502」——
    # 能力层必须与 409 门控、策略层给出**同一个**结论，否则会出现「按钮亮着、点了 409」。
    c = store.derive_capabilities(mk_probe(reachable=True, ok=False))
    for k in ("remark", "export", "images", "integrity"):
        check(c[k]["available"] is False,
              "业务 502：%s 判不可用（不再被传输层 reachable 掩盖）" % k, c[k])
    eq(c["remark"]["reason"], store._NEED_ANALYZER, "业务 502：reason 用统一文案")
    check("不可达" not in c["remark"]["reason"],
          "业务 502：文案不再谎称「当前不可达」（审查 ③）", c["remark"]["reason"])
    check(c["fetch"]["available"] is True and c["fetch"]["owner"] == "finder",
          "业务 502：fetch 降到 finder（sessdata=present）", c["fetch"])
    eq(store._plan_owner(mk_probe(reachable=True, ok=False), {}), "finder",
       "业务 502：_plan_owner 同样判 finder（与能力层一致）")
    eq(store._plan_owner(mk_probe(reachable=True, ok=True), {}), "analyzer",
       "可用时 _plan_owner 仍判 analyzer")


# ---------------------------------------------------------------- T2.2 接口码表

def t22_api_code_table():
    sec("T2.2 接口码表（阶段 1.5 ①）—— classify_api_code / classify_http_status 纯函数")
    # collector 顶层无 IO（只 import 标准库 + 定义常量/函数），与 store 同属可安全 import 的模块。
    import collector

    eq(collector.classify_api_code(-101)[0], "fatal", "-101 未登录 → fatal")
    eq(collector.classify_api_code(-111)[0], "fatal", "-111 csrf → fatal")
    eq(collector.classify_api_code(-412)[0], "backoff", "-412 风控 → backoff")
    eq(collector.classify_api_code(-509)[0], "backoff", "-509 限流 → backoff")
    eq(collector.classify_api_code(0), ("retry", ""), "未收录码 → 保守按 retry、无附注")
    eq(collector.classify_api_code(-99999)[0], "retry", "未知码 → retry（不因未知而放弃同步）")

    eq(collector.classify_http_status(412)[0], "backoff", "HTTP 412 → backoff")
    eq(collector.classify_http_status(429)[0], "backoff", "HTTP 429 → backoff")
    eq(collector.classify_http_status(500)[0], "retry", "HTTP 500 → retry")
    eq(collector.classify_http_status(404), ("retry", ""), "未收录 HTTP 码 → retry")

    # 码表必须能答出可读中文（用于日志 / 前端 hint），不是只有分类
    check(collector.classify_api_code(-412)[1], "风控类码带可读说明", collector.classify_api_code(-412))


# ---------------------------------------------------------------- T2.5 跨度说明

def t25_span_advisory():
    sec("T2.5 span_advisory —— 结构性跨度差的说明（纯函数，**不参与决策**）")

    a = store.span_advisory({"analyzer_days": 2452, "local_days": 134})
    check(a is not None, "主源 2452 / 本地 134 → 有 advisory")
    eq(a["kind"], "structural_span_gap", "kind = structural_span_gap（正常结构性差异）")
    eq(a["action"], "none", "结构性情形建议动作 = none（无需动作）")
    eq(a["gap_days"], 2318, "gap_days = 2318")
    check("迁移" in a["text"] or "P2" in a["text"], "文案指向一次性迁移（P2），而非'多同步几次'", a["text"])

    a = store.span_advisory({"analyzer_days": 2452, "local_days": 5})
    eq(a["kind"], "local_span_short", "本地仅 5 天 → local_span_short（疑似被清空）")
    eq(a["action"], "rebuild_local", "建议动作 = rebuild_local")
    check("备份" in a["text"] or "重跑" in a["text"], "文案给出可执行动作", a["text"])

    eq(store.span_advisory({"analyzer_days": 200, "local_days": 134}), None,
       "差距 66 天 ≤ 90 → 不提示")
    eq(store.span_advisory({"analyzer_days": None, "local_days": 134}), None,
       "缺 analyzer_days → 不提示")
    eq(store.span_advisory({"analyzer_days": 2452, "local_days": None}), None,
       "缺 local_days → 不提示")
    eq(store.span_advisory({}), None, "空 span → 不提示")
    eq(store.span_advisory(None), None, "None → 不提示")


# ---------------------------------------------------------------- T3 策略层

def t26_analyzer_skippable(tmp):
    """阶段 2 修正 A2：探测结论必须**会失效**，且不能只凭「HTTP 不可达」就跳过读文件。"""
    sec("T2.6 _analyzer_skippable —— 跳过主源读取的三条前置（TTL / mtime / 撤销）")
    a = os.path.join(tmp, "fake_analyzer.db")
    l = os.path.join(tmp, "fake_local.db")
    for p in (a, l):
        with open(p, "wb") as f:
            f.write(b"x")
    os.utime(a, (NOW, NOW))
    os.utime(l, (NOW + 10, NOW + 10))          # 本地库更新 → 主源不新

    oa, ol = store.ANALYZER_DB, store.LOCAL_DB
    ottl = store.ANALYZER_PROBE_TTL
    oprobe = dict(store._ANALYZER_PROBE)
    try:
        store.ANALYZER_DB, store.LOCAL_DB = a, l
        store.note_analyzer_usable(None)
        eq(store._analyzer_skippable(), False, "尚未探测 → 照读（不跳过）")
        store.note_analyzer_usable(True)
        eq(store._analyzer_skippable(), False, "结论=可达 → 不跳过")
        store.note_analyzer_usable(False)
        eq(store._analyzer_skippable(), True, "不可达 + 未过期 + 主源不新 → 可跳过")
        os.utime(a, (NOW + 100, NOW + 100))
        eq(store._analyzer_skippable(), False,
           "主源 mtime 更新（迁移后又被 Analyzer 写过）→ **不得跳过**")
        os.utime(a, (NOW, NOW))
        store._ANALYZER_PROBE["at"] = int(time.time()) - 400
        eq(store._analyzer_skippable(), False, "结论过期（> TTL）→ 不再跳过（A2 的核心修正）")
        store.note_analyzer_usable(None)
        eq(store._analyzer_skippable(), False, "撤销（None）→ 回到尚未探测")
        store.note_analyzer_usable(False)
        store.ANALYZER_DB = os.path.join(tmp, "nope.db")
        eq(store._analyzer_skippable(), False, "主源 mtime 读不到 → 保守不跳过")
    finally:
        store.ANALYZER_DB, store.LOCAL_DB = oa, ol
        store.ANALYZER_PROBE_TTL = ottl
        store._ANALYZER_PROBE.update(oprobe)


def t27_analyzer_gate():
    sec("T2.7 analyzer_gate_blocked —— 阶段 3 门控：滞回 + 恢复不对称 + 绝不 fail-closed")
    ogate = dict(store._ANALYZER_GATE)
    ottl = store.ANALYZER_PROBE_TTL
    ostreak = store.ANALYZER_GATE_STREAK
    try:
        def reset():
            store._ANALYZER_GATE.update({"ok": True, "streak": 0, "at": 0})

        # ---- 绝不 fail-closed：拿不到确定结论一律放行 ----
        reset()
        eq(store.analyzer_gate_blocked(), False, "从未探测 → 放行（未知 ≠ 不可用）")
        reset()
        store._ANALYZER_GATE["at"] = int(time.time()) - (ottl + 60)
        store._ANALYZER_GATE.update({"ok": False, "streak": 9})
        eq(store.analyzer_gate_blocked(), False,
           "记账过期 → 放行（陈旧结论不得永久挡住用户）")

        # ---- 滞回：连续 N 次才拦 ----
        eq(ostreak, 2, "默认滞回阈值 = 2（可用常量调整，但须 >1）")
        reset()
        eq(store.note_analyzer_gate_probe(False), 1, "第 1 次失败 → streak=1")
        eq(store.analyzer_gate_blocked(), False, "只失败 1 次 → 放行（抖动不足以定性）")
        eq(store.note_analyzer_gate_probe(False), 2, "第 2 次失败 → streak=2")
        eq(store.analyzer_gate_blocked(), True, "连续失败达阈值 → 拦截")

        # ---- 恢复不对称：1 次成功立刻解封 ----
        eq(store.note_analyzer_gate_probe(True), 0, "1 次成功 → streak 立刻归零")
        eq(store.analyzer_gate_blocked(), False, "恢复不对称：不等 TTL 即放行")

        # ---- streak 单调累加，不因次数多而动摇 ----
        reset()
        for i in range(1, 6):
            eq(store.note_analyzer_gate_probe(False), i, "连续失败第 %d 次 → streak=%d" % (i, i))
        eq(store.analyzer_gate_blocked(), True, "连续 5 次失败 → 仍拦截（结论稳定）")

        # ---- 边界：at=0 但 streak>0（记账被清空过的畸形状态）不得误拦 ----
        store._ANALYZER_GATE.update({"ok": False, "streak": 3, "at": 0})
        eq(store.analyzer_gate_blocked(), False, "at=0（从未记账）→ 放行，哪怕 streak 非零")

        # ---- `ok` 严格取 True 才算成功（避免非 bool 真值误判为「可用」）----
        # 注意：这些用例必须**同时保留足够的 streak**，否则会先被「证据不足」那条判据放行，
        # 测不到 `ok` 的比较方式 —— 那正是最初写错的地方（造了一组恒真的断言）。
        for truthy in (1, "yes", [1], "True"):
            reset()
            store.note_analyzer_gate_probe(False)
            store.note_analyzer_gate_probe(False)
            store._ANALYZER_GATE.update({"ok": truthy, "at": int(time.time())})
            eq(store.analyzer_gate_blocked(), True,
               "ok=%r 非 bool True → **不**算成功，仍拦截（严格比较）" % (truthy,))
        reset()
        store.note_analyzer_gate_probe(False)
        store.note_analyzer_gate_probe(False)
        store._ANALYZER_GATE.update({"ok": True, "at": int(time.time())})
        eq(store.analyzer_gate_blocked(), False,
           "ok=True（唯一的成功形态）→ 放行，即使 streak 已满（恢复优先）")
    finally:
        store.ANALYZER_PROBE_TTL = ottl
        store.ANALYZER_GATE_STREAK = ostreak
        store._ANALYZER_GATE.update(ogate)


def t3_decide_sync_plan():
    sec("T3 decide_sync_plan —— 规则表 R1–R6 + 覆盖（唯一决定增量/全量的地方）")

    base = dict(now=NOW, last_success_at=NOW - 86400, last_full_at=NOW - 86400 * 30,
                last_incremental_no_baseline=False, local_count=2865)
    combo = mk_probe(reachable=True, a_days=2400, l_days=2400)  # 长尾齐 → R3 不触发

    def plan(state=None, probe=None, policy=None):
        s = dict(base)
        s.update(state or {})
        return store.decide_sync_plan(probe or combo, policy or {}, s)

    # R6 默认
    eq(plan()["mode"], "incremental", "R6 默认 → incremental")
    eq(plan()["reason"], "常规增量", "R6 reason")
    eq(plan()["owner"], "analyzer", "组合形态 owner=analyzer")

    # R1 首次建基线（两种触发）
    eq(plan({"last_success_at": None})["mode"], "full", "R1a last_success_at 缺失 → full")
    check("首次建基线" in plan({"last_success_at": None})["reason"], "R1a reason")
    eq(plan({"local_count": 0})["mode"], "full", "R1b 本地库为空 → full")

    # R2 缺基线自动升级
    r = plan({"last_incremental_no_baseline": True})
    eq(r["mode"], "full", "R2 上次增量报缺基线 → full")
    check("缺基线" in r["reason"], "R2 reason")

    # R2 的**冷却收敛**（阶段 2 修正 A1）：缺基线不该让冷却形同不存在
    r = plan({"last_incremental_no_baseline": True, "last_full_at": NOW - 300})
    eq(r["mode"], "skip", "R2 命中但 5 分钟前刚全量过 → skip（不再连点全量）")
    check("冷却" in r["reason"], "R2 收敛 reason 说明冷却中", r["reason"])

    # R3 长尾缺口 —— 判据是"**本地库丢过数据**"（当前跨度 vs 历史峰值）
    r = plan(probe=mk_probe(reachable=True, a_days=2400, l_days=30),
             state={"span_peak_days": 2400})
    eq(r["mode"], "full", "R3 本地跨度 30 < 峰值 2400（丢过数据）→ full")
    check("峰值" in r["reason"], "R3 reason 说明是与峰值比", r["reason"])
    eq(plan(probe=mk_probe(reachable=True, a_days=2400, l_days=90),
            state={"span_peak_days": 100})["mode"], "incremental",
       "R3 跨度回退 10 天 < 阈值 30 → 不触发")
    eq(plan(probe=mk_probe(reachable=True, a_days=2400, l_days=90))["mode"], "incremental",
       "R3 无 span_peak_days 记录 → 不触发（阶段 1 的保守默认）")
    eq(plan(probe=mk_probe(reachable=True, a_days=2400, l_days=30),
            state={"span_peak_days": 2400, "last_full_at": NOW - 86400})["mode"], "incremental",
       "R3 命中但距上次全量仅 1 天（未 stale）→ 落到 R6")
    eq(plan(probe=mk_probe(reachable=True, a_days=2400, l_days=30),
            state={"span_peak_days": 2400, "last_full_at": None})["mode"], "full",
       "R3 last_full_at 为 None → 视为 stale → full")
    # ⭐ 反向护栏：**结构性跨度差不得触发 R3** —— 实测主源 2452 天 / 本地 134 天。
    # 若拿"主源深度 − 本地深度"当缺口，R3 会在任何状态下命中 → 每 7 天白跑一次全量。
    eq(plan(probe=mk_probe(reachable=True, a_days=2452, l_days=134))["mode"], "incremental",
       "R3 护栏：主源 2452 / 本地 134 的结构性差异**不触发** full")
    eq(plan(probe=mk_probe(reachable=True, a_days=2452, l_days=134),
            state={"span_peak_days": None})["mode"], "incremental",
       "R3 护栏（显式 None 峰值）")

    # R4 防连点
    r = plan({"last_full_at": NOW - 300}, );
    eq(r["mode"], "skip", "R4 距上次全量 5 分钟 < 冷却 10 分钟 → skip")
    check("冷却" in r["reason"], "R4 reason")
    eq(plan({"last_full_at": NOW - 3600})["mode"], "incremental",
       "R4 距上次全量 1 小时 > 冷却 → 不比 skip")

    # R5 凭证闸门（两处可触发，且不可被 override 绕过）
    r = store.decide_sync_plan(mk_probe(reachable=False, fsess="missing"), {}, dict(base))
    eq(r["mode"], "blocked", "R5a 独立形态 + 凭证缺失 → blocked")
    check("SESSDATA" in r["reason"], "R5a reason")
    eq(r["owner"], "finder", "R5a owner=finder")
    r = store.decide_sync_plan(mk_probe(reachable=True, fsess="missing"),
                               {"prefer": "local"}, dict(base))
    eq(r["mode"], "blocked", "R5b prefer=local + 凭证缺失 → blocked")
    r = store.decide_sync_plan(mk_probe(reachable=False, fsess="missing"),
                               {"sync": "full"}, dict(base))
    eq(r["mode"], "blocked", "R5 不可被 sync override 绕过（安全闸门前置）")
    r = store.decide_sync_plan(mk_probe(reachable=False, fsess="present"),
                               {"sync": "full"}, dict(base))
    eq(r["mode"], "full", "R5 通过后 override 才生效")
    # 阶段 1.5 ④：valid 与 present 同放行；invalid 明确 blocked（reason 说明已验证失效）
    eq(store.decide_sync_plan(mk_probe(reachable=False, fsess="valid"), {}, dict(base))["mode"],
       "incremental", "R5：valid 放行（联网验证通过 → 不 blocked）")
    r = store.decide_sync_plan(mk_probe(reachable=False, fsess="invalid"), {}, dict(base))
    eq(r["mode"], "blocked", "R5：invalid → blocked")
    check("失效" in r["reason"], "R5：invalid 的 reason 说明已验证失效", r["reason"])

    # 显式覆盖（Q3 的隐藏 override）
    r = plan(policy={"sync": "full"})
    eq(r["mode"], "full", "override sync=full → full")
    check("override" in r["reason"], "override full 的 reason 标注来源", r["reason"])
    r = plan(policy={"sync": "incremental"}, state={"last_full_at": NOW - 300})
    eq(r["mode"], "incremental", "override 优先于 R4（不进冷却判定）")

    # owner 分支
    eq(plan(policy={"prefer": "local"}, probe=mk_probe(reachable=True))["owner"], "finder",
       "prefer=local → owner=finder")
    eq(plan(probe=mk_probe(reachable=False, fsess="present"))["owner"], "finder",
       "独立形态 → owner=finder")
    eq(plan(policy={"prefer": "auto"}, probe=mk_probe(reachable=True))["owner"], "analyzer",
       "prefer=auto + 可达 → owner=analyzer")

    # 脏入参不炸
    store.decide_sync_plan(None, None, None)
    check(True, "全 None 入参不抛异常")
    store.decide_sync_plan({}, {}, {})
    check(True, "全空 dict 入参不抛异常")

    # ---- 一致性地板：`capabilities.fetch` 与 `plan.mode` 必须互相自洽 ----
    # （两处共用同一判据；任一侧写歪，这里立刻报红 —— 这是"能力层是唯一判断处"的护栏）
    for reachable, fsess in ((True, "unknown"), (True, "present"), (False, "missing"),
                             (False, "unknown"), (False, "present"),
                             (False, "valid"), (False, "invalid")):
        pr = mk_probe(reachable=reachable, fsess=fsess)
        can = store.derive_capabilities(pr)["fetch"]["available"]
        mode = store.decide_sync_plan(pr, {}, dict(base))["mode"]
        check((mode != "blocked") == can,
              "一致性：reachable=%s fsess=%s → fetch.available=%s / plan.mode=%s"
              % (reachable, fsess, can, mode))


# ---------------------------------------------------------------- T4 跨度读取（临时库）

def _mk_sqlite(path, ddl_list):
    con = sqlite3.connect(path)
    try:
        for ddl in ddl_list:
            con.execute(ddl)
        con.commit()
    finally:
        con.close()


def t4_span_and_meta(tmp):
    sec("T4 local_span_days / analyzer_span_days / local_meta（临时库，不碰真实数据）")

    # ---- 本地库 ----
    lp = os.path.join(tmp, "local.db")
    _mk_sqlite(lp, [
        "CREATE TABLE history (kid TEXT PRIMARY KEY, bvid TEXT, view_at INTEGER)",
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)",
    ])
    con = sqlite3.connect(lp)
    con.executemany("INSERT INTO history (kid,bvid,view_at) VALUES (?,?,?)", [
        ("a", "BV1", NOW - 100 * 86400),
        ("b", "BV2", NOW - 50 * 86400),
        ("c", "BV3", NOW - 1 * 86400),
    ])
    con.executemany("INSERT INTO meta (key,value) VALUES (?,?)", [
        ("last_success_at", str(NOW - 3600)),
        ("last_full_sync", str(NOW - 86400 * 3)),
    ])
    con.commit()
    con.close()

    s = store.local_span_days(lp, now=NOW)
    eq(s["count"], 3, "local_span_days: count=3")
    eq(s["oldest"], NOW - 100 * 86400, "local_span_days: oldest = 最早一条")
    eq(s["days"], 100, "local_span_days: days=100（R3 的度量）")

    eq(store.local_meta(["last_success_at", "last_full_sync"], lp)["last_success_at"],
       str(NOW - 3600), "local_meta: 读到 last_success_at")
    eq(store.local_meta(["no_such_key"], lp), {}, "local_meta: 缺失键不出现")

    # 库不存在 / 空库 / 脏路径都不炸
    miss = store.local_span_days(os.path.join(tmp, "nope.db"), now=NOW)
    eq((miss["count"], miss["days"]), (0, None), "local_span_days: 库不存在 → count=0/days=None")
    eq(store.local_meta(["x"], os.path.join(tmp, "nope.db")), {}, "local_meta: 库不存在 → {}")
    eq(store.local_span_days(os.path.join(tmp, "nope_dir", "nope.db"), now=NOW)["count"], 0,
       "local_span_days: 路径不存在 → 0")

    # ---- Analyzer 主源（跨年表） ----
    ap = os.path.join(tmp, "analyzer.db")
    _mk_sqlite(ap, [
        "CREATE TABLE bilibili_history_2020 (kid INTEGER, bvid TEXT, view_at INTEGER)",
        "CREATE TABLE bilibili_history_2026 (kid INTEGER, bvid TEXT, view_at INTEGER)",
        "CREATE TABLE bilibili_history_2026_fts (content TEXT)",  # 干扰项：应被排除
    ])
    con = sqlite3.connect(ap)
    con.execute("INSERT INTO bilibili_history_2020 VALUES (0,'BVX',?)", (NOW - 2400 * 86400,))
    con.execute("INSERT INTO bilibili_history_2026 VALUES (0,'BVY',?)", (NOW - 10 * 86400,))
    con.execute("INSERT INTO bilibili_history_2026 VALUES (0,'BVZ',?)", (NOW - 5 * 86400,))
    con.commit()
    con.close()

    s = store.analyzer_span_days(ap, now=NOW)
    eq(s["count"], 3, "analyzer_span_days: 跨年表汇总 count=3")
    eq(s["tables"], 2, "analyzer_span_days: 年表 2 张（_fts 虚拟表被排除）")
    eq(s["days"], 2400, "analyzer_span_days: days=2400（最早那条）")
    eq(store.analyzer_span_days(os.path.join(tmp, "nope.db"), now=NOW)["count"], 0,
       "analyzer_span_days: 库不存在 → 0")

    # ---- R3 的真实数据流：用两个临时库拼一个 probe ----
    probe = mk_probe(reachable=True, a_days=s["days"], l_days=100)
    _base = {"now": NOW, "last_success_at": NOW - 3600,
             "last_full_at": NOW - 86400 * 30, "local_count": 3}
    # ⭐ 护栏：临时库算出的"结构性跨度差"(2400 vs 100) **不得**触发 full
    eq(store.decide_sync_plan(probe, {}, _base)["mode"], "incremental",
       "R3 端到端护栏：结构性跨度差（2400 vs 100）不触发 full")
    # 而 advisory 要把它说清楚（指向 P2 迁移，而不是"多点几次同步"）
    ad = store.span_advisory(probe["data"]["span"])
    check(ad is not None and ad["gap_days"] == 2300, "端到端：advisory 算出 gap_days=2300", ad)
    eq(ad["action"], "none", "端到端：本地 100 天 → 视为结构性（action=none）")
    # 真的丢过数据时（峰值 > 当前）才触发 full
    eq(store.decide_sync_plan(probe, {}, dict(_base, span_peak_days=2400))["mode"], "full",
       "R3 端到端：峰值 2400 vs 当前 100 → full")


# ---------------------------------------------------------------- T5 策略文件读写

def t5_policy_io(tmp):
    sec("T5 load_policy / save_policy（临时文件）")

    p = os.path.join(tmp, "source_config.json")

    # 1) 旧格式文件：只读兼容
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"mode": "local",
                   "analyzer_db": r"D:\some\bilibili_history.db"}, f)
    pol = store.load_policy(p)
    eq(pol["prefer"], "local", "load_policy: 旧 mode=local → prefer=local")

    # 2) 写回：保留 analyzer_db + 同步旧 mode 字段（否则旧读法失效）
    store.save_policy({"prefer": "auto", "sync": "auto",
                       "rules": {"full_cooldown_min": 15}}, p)
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    eq(raw["analyzer_db"], r"D:\some\bilibili_history.db", "save_policy: 保留 analyzer_db")
    eq(raw["mode"], "auto", "save_policy: 同步旧 mode 字段（向后兼容旧读法）")
    eq(raw["policy"]["rules"]["full_cooldown_min"], 15, "save_policy: 写入 rules")
    eq(raw["policy"]["rules"]["full_interval_days"], 7, "save_policy: 未给的 rules 键补默认")
    eq(store.load_policy(p)["rules"]["full_cooldown_min"], 15, "load_policy: 往返一致")

    # 3) 写回 local 时 mode 同步为 local
    store.save_policy({"prefer": "local"}, p)
    with open(p, "r", encoding="utf-8") as f:
        raw = json.load(f)
    eq((raw["mode"], raw["policy"]["prefer"]), ("local", "local"),
       "save_policy: prefer=local 时 mode 也写 local")

    # 4) 文件不存在 / 内容损坏 → 回落默认，不炸
    eq(store.load_policy(os.path.join(tmp, "nope.json"))["prefer"], "auto",
       "load_policy: 文件不存在 → 默认")
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{not json")
    eq(store.load_policy(bad)["prefer"], "auto", "load_policy: 内容损坏 → 默认")

    # 5) 只读真实配置：确认存在时不抛（**只读，不写**）
    try:
        real = store.load_policy()
        check(isinstance(real, dict) and real.get("prefer") in ("auto", "local"),
              "load_policy(): 真实配置可读且值合法（只读，未写盘）")
    except Exception as e:  # noqa
        check(False, "load_policy(): 读真实配置抛异常", e)


# ---------------------------------------------------------------- T6 文档一致性

def t6_contract_surface():
    sec("T6 契约面自检")

    # 能力语义表必须覆盖 CAPABILITY_KEYS（防止"加了能力忘写说明"）
    eq(sorted(store.CAPABILITY_DOC.keys()), sorted(store.CAPABILITY_KEYS),
       "CAPABILITY_DOC 与 CAPABILITY_KEYS 一一对应")

    # 策略默认值必须含 prefer/sync/rules 三段，且 rules 的键都被规则表用到
    eq(sorted(store.POLICY_DEFAULT.keys()), ["prefer", "rules", "sync"],
       "POLICY_DEFAULT 结构 = prefer / sync / rules")
    for k in ("full_cooldown_min", "full_interval_days", "gap_threshold_days"):
        check(k in store.POLICY_DEFAULT["rules"], "POLICY_DEFAULT.rules 含 %s" % k)

    # 关键函数签名：喂 dict 即可测（纯函数层的存在理由）
    import inspect
    for fn, n in ((store.derive_capabilities, 1), (store.decide_sync_plan, 3),
                  (store.normalize_policy, 1), (store.span_advisory, 1)):
        sig = inspect.signature(fn)
        req = [p for p in sig.parameters.values()
               if p.default is inspect.Parameter.empty
               and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        eq(len(req), n, "%s 必需位置参数 = %d 个（保持可测）" % (fn.__name__, n))


# ---------------------------------------------------------------- T7 可选：本机只读冒烟

def t7_live_smoke():
    sec("T7 --live：/api/capabilities 端到端冒烟（只读 GET，不打 B站）")
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(base, p):
        with opener.open(base + p, timeout=40) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))

    httpd = None
    base = "http://127.0.0.1:8765"
    try:
        get(base, "/api/capabilities?sessdata=0")
        print("  目标：本机 8765（你正在运行的实例）")
    except Exception:
        # 8765 没在跑 → **进程内**起一个临时实例（随机端口、只绑 127.0.0.1、
        # 不走 main() 故不建目录/不写盘）。这条路径让本测试随时可跑，不必先手动起服务。
        try:
            import threading
            from http.server import ThreadingHTTPServer
            import server
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            base = "http://127.0.0.1:%d" % httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            print("  目标：进程内临时实例 %s（8765 未在运行）" % base)
        except Exception as e:  # noqa
            print("  ! 跳过：8765 未运行，且临时实例起不来（%s）" % e)
            return

    try:
        st, body = get(base, "/api/capabilities?sessdata=0")
        eq(st, 200, "GET /api/capabilities → 200")
        eq(body.get("ok"), True, "响应 ok=true")
        check(body.get("mode") in ("combined", "standalone"),
              "mode ∈ combined|standalone", body.get("mode"))
        eq(tuple((body.get("capabilities") or {}).keys()), store.CAPABILITY_KEYS,
           "响应 capabilities 键集合与 CAPABILITY_KEYS 一致")
        for k in ("connection", "data", "policy", "plan", "sessdata", "capabilities", "advisory"):
            check(k in body, "响应含新字段 %s" % k)
        # 超集兼容（M4）：旧 /api/fetcher-health 的顶层字段必须还在
        for k in ("reachable", "status", "source"):
            check(k in body, "超集兼容：响应仍含旧字段 %s" % k)
        check("span" in (body.get("data") or {}), "data.span 存在（R3 的输入）")
        check("gap_days" in ((body.get("data") or {}).get("span") or {}),
              "data.span.gap_days 存在（结构性跨度差，仅供展示）")
        check("peak_days" in ((body.get("data") or {}).get("span") or {}),
              "data.span.peak_days 存在（R3 的真正输入）")
        check((body.get("plan") or {}).get("mode") in ("full", "incremental", "skip", "blocked"),
              "plan.mode 取值合法", (body.get("plan") or {}).get("mode"))
        check("sessdata" in (body.get("connection", {}).get("finder") or {}),
              "connection.finder.sessdata 存在")
        # ⭐ 契约对齐（这条抓到过一个真 bug）：用**真实产出的 finder 段**去驱动能力层，
        # 验证键名没漂移 —— 曾经 finder 段叫 `state`、能力层读 `sessdata`，导致独立形态下
        # `fetch` 恒判 unknown；而 Analyzer 可达时被 reachable 分支掩盖，只有这条能发现。
        fin = (body.get("connection") or {}).get("finder") or {}
        check("sessdata" in fin, "connection.finder 含能力层读取的键 sessdata")
        check(fin.get("sessdata") in ("missing", "present"),
              "finder.sessdata 值域 ⊂ {missing, present}（阶段 1 不联网）", fin.get("sessdata"))
        cap_solo = store.derive_capabilities(
            {"connection": {"analyzer": {"reachable": False}, "finder": fin}})
        if fin.get("sessdata") == "present":
            check(cap_solo["fetch"]["available"] is True,
                  "契约：独立形态 + 真实 finder 段(present) → fetch 可用（键名对齐）")
        elif fin.get("sessdata") == "missing":
            check(cap_solo["fetch"]["available"] is False,
                  "契约：独立形态 + 真实 finder 段(missing) → fetch 不可用")
        # R3 护栏：实测本机是结构性跨度差 → 不该因此触发 full
        span = (body.get("data") or {}).get("span") or {}
        if span.get("peak_days") is None and (body.get("policy") or {}).get("sync") == "auto":
            check((body.get("plan") or {}).get("mode") != "full"
                  or "峰值" in ((body.get("plan") or {}).get("reason") or ""),
                  "R3 护栏：无峰值记录时不得因结构性跨度差触发 full",
                  (body.get("plan") or {}).get("mode"))

        # 旧端点行为不变（阶段 1 的核心承诺）
        st2, old = get(base, "/api/fetcher-health?sessdata=0")
        eq(st2, 200, "旧 GET /api/fetcher-health → 200（行为不变）")
        for k in ("ok", "reachable", "source"):
            check(k in old, "旧端点仍含 %s" % k)
        # `status` 不走 Finder 自己拼装 —— 它**原样来自 Analyzer 的 /health 响应**，
        # 所以只有 Analyzer 在跑时才有这个键；Analyzer 不可达时是优雅降级体。
        # （2026-10-01 修：此处原写死 `status` 必须在，等于把"Analyzer 正在运行"当成了
        #  测试前提 —— Analyzer 一停就误报失败，属"环境依赖"型假失败。）
        if old.get("reachable"):
            check("status" in old, "旧端点仍含 status（Analyzer 可达时原样透传）")
        else:
            check("status" not in old and old.get("reachable") is False,
                  "Analyzer 不可达 → 优雅降级体（不伪造 status）",
                  "键=%s" % sorted(old.keys()))
        st3, oldds = get(base, "/api/data-source")
        eq(st3, 200, "旧 GET /api/data-source → 200（行为不变）")
        check("modes" in oldds and "mode" in oldds, "旧端点仍含 mode/modes")

        print("  摘要：mode=%s  plan=%s(%s)"
              % (body.get("mode"), (body.get("plan") or {}).get("mode"),
                 (body.get("plan") or {}).get("reason")))
        print("        span: analyzer=%s天 local=%s天 gap=%s天  advisory=%s"
              % (span.get("analyzer_days"), span.get("local_days"), span.get("gap_days"),
                 (body.get("advisory") or {}).get("kind")))
        print("        data: analyzer=%s local=%s merged=%s effective=%s"
              % ((body.get("data") or {}).get("analyzer"), (body.get("data") or {}).get("local"),
                 (body.get("data") or {}).get("merged"), (body.get("data") or {}).get("effective")))
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            print("  （临时实例已关闭，不留后台进程）")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="「连接即模式」阶段 1 纯函数单测")
    ap.add_argument("--live", action="store_true",
                    help="额外对 127.0.0.1:8765/api/capabilities 做只读结构冒烟")
    args = ap.parse_args()

    print("=" * 68)
    print("  「连接即模式」阶段 1 单测 —— 纯函数层（零 IO / 不联网 / 不碰真实库）")
    print("=" * 68)

    t1_normalize_policy()
    t2_derive_capabilities()
    t22_api_code_table()
    t25_span_advisory()
    t3_decide_sync_plan()
    t27_analyzer_gate()

    tmp = tempfile.mkdtemp(prefix="bhf_test_")
    try:
        t26_analyzer_skippable(tmp)
        t4_span_and_meta(tmp)
        t5_policy_io(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print("\n  （临时目录已清理：%s）" % tmp)

    t6_contract_surface()
    if args.live:
        t7_live_smoke()

    print("\n" + "=" * 68)
    if _FAIL:
        print("  结果：%d 通过 / %d 失败" % (_OK, _FAIL))
        print("  失败项：")
        for f in _FAILED:
            print("    - %s" % f)
        print("=" * 68)
        return 1
    print("  结果：全部通过（%d 项断言）" % _OK)
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
