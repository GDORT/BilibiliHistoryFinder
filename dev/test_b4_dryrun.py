# -*- coding: utf-8 -*-
"""B4 预演：对全量数据 dry-run 规则应用，输出「会改哪些」。

只读 —— 不写任何库。用于在真打 /api/apply-rules 之前确认影响面。
"""
import os
import sys
import io
import json
import time
import sqlite3
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import store  # noqa: E402
import engine  # noqa: E402


def main():
    rules = engine.load_rules_from_file(os.path.join(ROOT, "data", "rules.json"))
    rule = engine.get_active_rule(rules)
    state = store.load_state()
    raw = store.get_raw()
    now = int(time.time())

    print("=== B4 预演（dry-run，不写库）===")
    print("  规则：%s（%d 组）" % (rule.get("label") or rule.get("id"), len(rule.get("groups", []))))
    print("  全量记录：%d 条" % len(raw))
    print()

    # 逐条 evaluate_rule，统计「当前 vs 修好后」判定差异
    res = collections.Counter()
    changed = []
    for r in raw:
        hit = engine.evaluate_rule(r, rule, now)
        act, label = hit if hit else (None, None)
        res[("hit" if act else "miss", label)] += 1
        # 与已落库值对比
        cur_skip = 1 if r.get("auto_skip") else 0
        new_skip = 1 if act == "auto_skip" else 0
        cur_reason = r.get("auto_skip_reason") or None
        new_reason = ("auto_skip::%s" % label) if act == "auto_skip" else (
            ("stale::%s" % label) if act == "stale" else None)
        if cur_skip != new_skip or (cur_reason or "") != (new_reason or ""):
            changed.append((r.get("kid"), cur_reason, new_reason, r.get("duration"), r.get("progress")))

    print("=== 重算判定分布 ===")
    for (kind, label), n in sorted(res.items(), key=lambda x: -x[1]):
        tag = "%s" % (label or "—")
        print("  %-6s %-18s %5d" % (kind, tag, n))
    print()

    # store.apply_rules(dry=True) 给权威口径
    res2 = store.apply_rules(raw, rule, state, dry=True)
    print("=== store.apply_rules(dry=True) 权威口径 ===")
    print("  ok           =", res2.get("ok"))
    print("  total        =", res2.get("total"))
    print("  auto_set     =", res2.get("auto_set"), "← 会被标成「已跳过」")
    print("  stale_set    =", res2.get("stale_set"))
    print("  manual_cleared =", res2.get("manual_cleared"))
    print()

    print("=== 与已落库值的差异（真正会被改的）===")
    print("  有差异的记录：%d 条 / %d" % (len(changed), len(raw)))
    c = collections.Counter()
    for kid, cur, new, dur, prog in changed:
        if (cur or "") == (new or ""):
            c["仅桶变化（标记位）"] += 1
        else:
            c["理由变化"] += 1
    for k, v in c.most_common():
        print("    %-22s %d" % (k, v))
    print()

    print("=== 关键校验：#41 修复后「短视频碎片」误伤应为 0 ===")
    fs = res[("hit", "短视频碎片")]
    fs_mis = [x for x in changed if x[2] and "短视频碎片" in (x[2] or "") and (x[3] in (0, None))]
    print("  重算命中「短视频碎片」总数：%d" % fs)
    print("  其中 duration 缺失(0/None)：%d" % len(fs_mis))
    print("  ⇒ %s" % ("✅ 误伤为 0（#41 生效）" if len(fs_mis) == 0 else "❌ 仍有 %d 条误伤" % len(fs_mis)))
    print()

    print("=== 前 15 条差异样本 ===")
    for kid, cur, new, dur, prog in changed[:15]:
        print("  %-16s dur=%-6s prog=%-5s  %s → %s"
              % (str(kid)[:16], dur, prog, cur or "(无)", new or "(无)"))


if __name__ == "__main__":
    main()
