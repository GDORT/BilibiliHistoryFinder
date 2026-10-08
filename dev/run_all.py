# -*- coding: utf-8 -*-
"""统一测试 runner —— 「全绿」的**单一权威出处**。

解决 `doc/log/2026-10-06-评估-测试体系设计与执行.md` §七 B1/B3 指出的两个机制缺口：

- **B1 无统一 runner** → 各脚本各跑各的，「全绿」只能靠人工记账。
- **B3 声明无证据回链** → 文档写「289 项」，与实跑是否一致无从校验。

⚠️ **为什么必须带 `--env both`（默认）**：
2026-10-06 审查发现 `test_api_contract.py` 曾「本机有 `http_proxy` 时全绿、
无代理时 3 条恒红」—— 根因是 `Popen` 不传 `env` 导致子进程继承代理变量。
⇒ **本 runner 默认在「带代理」「无代理」两种环境各跑一遍**，两者都绿才算绿。
`--env once` 只跑当前环境（快，但**不能作为验收依据**）。

用法：
    python dev/run_all.py                 # 默认：两种环境各跑一遍 ＋ 声明校验
    python dev/run_all.py --with-a1       # 额外跑 A1 前端运行时冒烟（需先 . dev/e2e/env.ps1）
    python dev/run_all.py --env once      # 只跑当前环境（快速自检，勿用于验收）
    python dev/run_all.py --quiet         # 只出汇总表
    python dev/run_all.py --no-declared   # 跳过「文档声明 ↔ 实跑」校验
"""
import io
import os
import re
import sys
import json
import time
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV = os.path.join(ROOT, "dev")

# 代理变量：必须剥离 —— 否则子进程继承、测试结果随本机环境漂移（见模块 docstring）
PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")

# ⚠️ **故意排除的脚本**（评估稿 A3：它们不是回归测试，进 runner 会制造假保障）
#   · `test_b2_sandbox.py` / `test_b4_dryrun.py` —— **演示脚本，零断言**（只 print），
#     且它们的断言部分已被 A2 的 `test_finder_collect.py` 正式覆盖。
#   · `test_b2_live.py` / `test_b3_dryrun.py` / `backfill_duration.py` —— **真发 B站**
#     （消耗风控额度、需用户授权），按需执行，不进常规回归。
#   · `_probe_sessdata.py` —— 只读诊断工具，非测试。
EXCLUDED = {
    "test_b2_sandbox.py": "演示脚本（零断言）＋ 断言已被 test_finder_collect 覆盖",
    "test_b4_dryrun.py": "演示脚本（零断言）",
    "test_b2_live.py": "真发 B站（需授权 · 耗风控额度）",
    "test_b3_dryrun.py": "真发 B站（需授权）",
    "backfill_duration.py": "真发 B站 ＋ 数据操作（需授权）",
    "_probe_sessdata.py": "只读诊断工具，非测试",
}

# 参与「常规回归」的 8 套（B 组实网脚本不进，见 dev/README §6.5）
SUITES = [
    ("test_engine.py",         "规则引擎单测（零 IO）"),
    ("test_collector.py",      "采集器离线部分（临时库）"),
    ("test_api_contract.py",   "端点契约（沙箱 + 假 Analyzer）"),
    ("test_finder_collect.py", "Finder 自身抓取（沙箱 + 假 B站，零实网）"),
    ("test_capabilities.py",   "能力/门控/端点冒烟"),
    ("regression_fallback.py", "降级与回退链路"),
    ("regression_restart.py",  "重启链路"),
    ("verify_riskfix.py",      "风险修复验收（整进程隔离副本）"),
]

# 文档里声明的断言数 —— 与实跑交叉校验（B3 的「证据回链」）
# ⚠️ 只收**稳定口径**的数字：`--live` 会随端点增删浮动，不参与校验。
DECLARED = {
    "test_engine.py": 114,
    "test_collector.py": 47,
    "test_api_contract.py": 97,
    "test_finder_collect.py": 22,
    "test_capabilities.py": 289,
    "verify_riskfix.py": 97,
}

# ---- A1 前端运行时冒烟（2026-10-08 落地 MVP-3）---------------------------
# ⚠️ **刻意不放进 SUITES**：它需要**另一套解释器**（playwright 只装在
#    dev/e2e/pkgs，见 dev/e2e/env.ps1），而 run_one() 固定用 sys.executable。
#    硬塞进去会得到"ModuleNotFoundError: playwright"的假失败。
# ⚠️ 断言数 **54** 与 doc/log/2026-10-07-设计-前端运行时自动化测试.md §十 MVP-3
#    的规模一致（G1/G2/G3/G4，含确定性夹具与反例）。（已覆盖设计稿 §六 全部 6 组）。
A1_DECLARED = 54
A1_SCRIPT = os.path.join("dev", "e2e", "smoke_frontend.py")

RESULT_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
COUNT_RE = re.compile(r"全部通过（(\d+)\s*项断言）")


def clean_env():
    """剥离代理变量的环境副本 —— 让沙箱行为与「本机无代理」这一基准一致。"""
    return {k: v for k, v in os.environ.items() if k.lower() not in PROXY_KEYS}


def has_proxy():
    return any(k.lower() in os.environ for k in PROXY_KEYS) or \
           any(k.lower() in os.environ for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))


def run_one(script, label, env):
    """跑一套 → (ok, 实际断言数, 输出尾部, 耗时秒)"""
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, os.path.join(DEV, script)],
                           cwd=ROOT, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1200)
        out = (p.stdout or "") + (p.stderr or "")
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        return False, None, "超时（>1200s）", time.time() - t0
    dt = time.time() - t0
    tail = ""
    for ln in reversed(out.splitlines()):
        s = ln.strip()
        if s and ("结果：" in s or "总体：" in s or "FAIL" in s or "x " in s[:4]):
            tail = s
            break
    return ok, tail, out, dt


def parse_count(tail):
    """从结果行里取断言数（`97/97` 与「95 项断言」两种写法都认）。"""
    if not tail:
        return None
    m = COUNT_RE.search(tail)
    if m:
        return int(m.group(1))
    m = RESULT_RE.search(tail)
    if m and m.group(1) == m.group(2):
        return int(m.group(1))
    return None


def fingerprint_data():
    """data/ 全部文件 md5 —— 用于「跑完核零污染」"""
    import hashlib
    d = os.path.join(ROOT, "data")
    out = {}
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            try:
                out[f] = hashlib.md5(open(p, "rb").read()).hexdigest()[:12]
            except Exception:
                out[f] = "(unreadable)"
    return out


def run_a1(base_env):
    """跑 A1 前端冒烟 —— 与 SUITES 分开跑，因为它需要**另一套解释器＋另组环境变量**。

    `dev/e2e/env.ps1` 是 PowerShell 脚本，这里**不执行它**，而是逐行解析出里面的
    `$env:NAME = "value"` 用 —— 跨 shell，且调用方不必先 `. .`（更少出错面）。
    ⚠️ 解析用 `utf-8-sig`：setup.ps1 写出的文件**带 BOM**（实测）。
    """
    env = dict(base_env)
    env_file = os.path.join(ROOT, "dev", "e2e", "env.ps1")
    if not os.path.exists(env_file):
        return False, None, "缺 dev/e2e/env.ps1（先跑 setup.ps1）", 0.0
    for ln in io.open(env_file, encoding="utf-8-sig"):
        m = re.match(r'\$env:([A-Za-z_]+)\s*=\s*"(.*)"\s*$', ln.strip())
        if m:
            env[m.group(1)] = m.group(2)
    pkgs = env.get("PYTHONPATH", "")
    if not os.path.isdir(pkgs):
        return False, None, "env.ps1 的 PYTHONPATH 不存在：%s" % pkgs, 0.0

    # 解释器 = 装 playwright 的那套。优先取 setup.ps1 里写的那个；找不到就用当前
    # 解释器（用户若把 playwright 装进当前环境也能跑通）。
    pyexe = sys.executable
    setup = os.path.join(ROOT, "dev", "e2e", "setup.ps1")
    if os.path.exists(setup):
        m = re.search(r'\[string\]\$Python\s*=\s*"([^"]+)"',
                      io.open(setup, encoding="utf-8-sig").read())
        if m and os.path.exists(m.group(1)):
            pyexe = m.group(1)

    script = os.path.join(ROOT, A1_SCRIPT)
    t0 = time.time()
    try:
        p = subprocess.run([pyexe, script], cwd=ROOT, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=1800)
        out = (p.stdout or "") + (p.stderr or "")
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        return False, None, "超时（>1800s）", time.time() - t0
    dt = time.time() - t0
    tail = ""
    for ln in reversed(out.splitlines()):
        s = ln.strip()
        if s and "结果：" in s:
            tail = s
            break
    return ok, parse_count(tail), tail or "(无结果行)", dt


def main():
    argv = sys.argv[1:]
    quiet = "--quiet" in argv
    check_declared = "--no-declared" not in argv
    both = "--env" not in argv or "both" in argv
    with_a1 = "--with-a1" in argv
    stages = [("带代理", os.environ.copy()), ("无代理", clean_env())] if both \
        else [("当前环境", os.environ.copy())]

    print("=" * 68)
    print("  统一测试 runner  ·  「全绿」的单一权威出处")
    print("  项目：%s" % ROOT)
    print("  本机环境：%s" % ("**有代理变量**" if has_proxy() else "无代理变量"))
    print("  跑法：%s" % ("两种环境各跑一遍（验收口径）" if both else "**仅当前环境（不可作为验收依据）**"))
    print("=" * 68)

    print("\n▸ 故意排除（不算「未跑」）")
    print("-" * 68)
    for k in sorted(EXCLUDED):
        print("  [排除] %-24s %s" % (k, EXCLUDED[k]))
    print()

    fp_before = fingerprint_data()
    results = {}          # (stage, script) -> dict
    a1_results = {}       # stage -> dict（A1 独立跑，见run_a1）
    all_ok = True

    for stage_name, env in stages:
        print("\n▸ 环境：%s" % stage_name)
        print("-" * 68)
        for script, label in SUITES:
            ok, tail, out, dt = run_one(script, label, env)
            n = parse_count(tail)
            results[(stage_name, script)] = {"ok": ok, "count": n, "tail": tail, "out": out, "dt": dt}
            if not ok:
                all_ok = False
            mark = "PASS" if ok else "FAIL"
            cnt = ("%3d 项" % n) if n else " n/a  "
            if not quiet:
                print("  [%s] %-24s %-8s %5.1fs  %s"
                      % (mark, script.replace(".py", ""), cnt, dt, label))
            else:
                print("  [%s] %-24s %-8s %5.1fs" % (mark, script.replace(".py", ""), cnt, dt))
            if not ok:
                for ln in out.splitlines():
                    if ln.strip().startswith("x ") or "FAIL" in ln:
                        print("        %s" % ln.strip()[:110])

    # ---- A1 前端运行时冒烟（可选组：--with-a1，默认跳过）----
    a1_runs = []
    if with_a1:
        print("\n▸ A1 前端运行时冒烟（可选组 · design MVP-3）")
        print("-" * 68)
        for stage_name, env in stages:
            ok, n, tail, dt = run_a1(env)
            a1_runs.append((stage_name, ok, n))
            a1_results[stage_name] = {"ok": ok, "count": n, "tail": tail, "dt": dt}
            if not ok:
                all_ok = False
            cnt = ("%3d 项" % n) if n else " n/a  "
            print("  [%s] %-24s %-8s %5.1fs  %s"
                  % ("PASS" if ok else "FAIL", "smoke_frontend", cnt, dt,
                     "前端运行时（G1/G2/G3/G4）"))
            if not ok:
                print("        %s" % str(tail)[:110])
    else:
        print("\n▸ A1 前端冒烟：未启用（加 --with-a1 开启；需先 . dev/e2e/env.ps1）")

    # ---- 跨环境一致性（本次事故的直接防线）----
    if both:
        print("\n▸ 跨环境一致性")
        print("-" * 68)
        consistent = True
        for script, _l in SUITES:
            a = results.get(("带代理", script), {})
            b = results.get(("无代理", script), {})
            same = (a.get("ok") == b.get("ok")) and (a.get("count") == b.get("count"))
            if not same:
                consistent = False
                all_ok = False
            print("  [%s] %-24s 带代理=%s/%s  无代理=%s/%s"
                  % ("一致" if same else "不一致", script.replace(".py", ""),
                     "过" if a.get("ok") else "红", a.get("count") or "n/a",
                     "过" if b.get("ok") else "红", b.get("count") or "n/a"))
        if a1_runs and len(a1_runs) == 2:
            same = a1_runs[0][1:] == a1_runs[1][1:]
            if not same:
                consistent = False
                all_ok = False
            print("  [%s] %-24s 带代理=%s/%s  无代理=%s/%s"
                  % ("一致" if same else "不一致", "smoke_frontend",
                     "过" if a1_runs[0][1] else "红", a1_runs[0][2] or "n/a",
                     "过" if a1_runs[1][1] else "红", a1_runs[1][2] or "n/a"))
        if not consistent:
            print("  ⚠️ 两种环境结果不同 → 说明仍有断言藏了环境前提（见 dev/README §三·7）")

    # ---- 文档声明 ↔ 实跑交叉校验（B3 的「证据回链」）----
    decl_bad = []
    a1_decl_bad = []       # A1 独立回链的结果（**分开口径**，见 A1 声明回链段）
    if check_declared:
        print("\n▸ 文档声明 ↔ 实跑交叉校验")
        print("-" * 68)
        for script, want in sorted(DECLARED.items()):
            # ⚠️ **只在「该脚本自己通过」时才校验断言数** —— 否则它一挂掉 count 为 None，
            #   会把**所有**脚本的声明都误判成「不一致」（2026-10-06 负向验证抓到的真 bug）。
            #   失败是「另一种不一致」，已由上面的 PASS/FAIL 栏与总判定兜住。
            # ⚠️ 取「**任一**环境的结果」—— 不能只查固定 key：`--env once` 时只有
            #   `("当前环境", script)` 这个键，否则会全部落空 → 全被误判「未通过」
            #   （2026-10-06 负向验证第二次抓到的真 bug）。
            r0 = {}
            for st, _e in stages:
                cand = results.get((st, script))
                if cand and cand.get("ok"):
                    r0 = cand
                    break
                if cand and not r0:
                    r0 = cand
            if not r0.get("ok"):
                print("  [跳过] %-24s 文档声明 %3d  该套未通过 → 断言数无从校验"
                      % (script.replace(".py", ""), want))
                continue
            got = r0.get("count")
            ok = (got == want)
            if not ok:
                decl_bad.append((script, want, got))
                all_ok = False
            print("  [%s] %-24s 文档声明 %3d  实跑 %s"
                  % ("一致" if ok else "不一致", script.replace(".py", ""), want,
                     ("%3d" % got) if got else "无"))
        if decl_bad:
            print("  ⚠️ 有 %d 处声明与实跑不符 → 文档需同步（这是「声明漂移」）" % len(decl_bad))

    # ---- A1 的声明回链（设计稿 §七：纳入验收时**必须**挂 B3，否则重演假绿）----
    if with_a1:
        a1_any = None
        for _st in a1_results:
            if a1_results[_st].get("ok"):
                a1_any = a1_results[_st]
                break
        if a1_any is None:
            print("\n▸ A1 声明回链")
            print("-" * 68)
            print("  [跳过] smoke_frontend          A1 未通过 → 断言数无从校验")
        else:
            got = a1_any.get("count")
            ok = (got == A1_DECLARED)
            # ⚠️ 必须记进 `a1_decl_bad` —— 否则日志那行「声明校验」只看6 套的
            #   `decl_bad`，A1 漂移时会显示「通过」却总判定❌（实测踩过：语义自相矛盾）。
            a1_decl_bad = []if ok else [(A1_DECLARED, got)]
            if not ok:
                all_ok = False
            print("\n▸ A1 声明回链")
            print("-" * 68)
            print("  [%s] %-24s 文档声明 %3d  实跑 %s"
                  % ("一致" if ok else "不一致", "smoke_frontend", A1_DECLARED,
                     ("%3d" % got) if got else "无"))
            if not ok:
                print("  ⚠️ A1 声明漂移 → 更新 run_all.py 的 A1_DECLARED 并同步文档")

    # ---- 零污染 ----
    fp_after = fingerprint_data()
    changed = [f for f in set(fp_before) | set(fp_after)
               if fp_before.get(f) != fp_after.get(f)]
    print("\n▸ 零污染核对")
    print("-" * 68)
    if changed:
        all_ok = False
        print("  ⚠️ data/ 有 %d 个文件被改动：%s" % (len(changed), "、".join(changed[:6])))
        print("     （`canonical_state.db` / `auto_skip_progress.json` 等被测试写入即属污染）")
    else:
        print("  ✅ data/ %d 个文件指纹全同" % len(fp_before))

    # ---- 汇总 ----
    total_n = 0
    for stage_name, _e in stages:
        for script, _l in SUITES:
            total_n += results[(stage_name, script)].get("count") or 0
    for stage_name in a1_results:
        total_n += a1_results[stage_name].get("count") or 0
    n_suites = len(SUITES) + (1 if with_a1 else 0)
    print("\n" + "=" * 68)
    if all_ok:
        extra = "（含 A1 前端冒烟）" if with_a1 else "（未启用 A1 前端冒烟）"
        print("  ✅ 全部通过（%d 套 × %d 环境，累计断言 %d 次）%s"
              % (n_suites, len(stages), total_n, extra))
        print("     （`regression_restart.py` 不报断言数，只报通过与否 → 记 n/a）")
        print("     ⚠️ 这是本仓「全绿」的**单一权威出处** —— 文档声明应与此处一致。")
    else:
        print("  ❌ 存在失败项（见上）")
    print("=" * 68)

    # ---- 追加过程日志（B2：独立测试过程时间线）----
    log = os.path.join(DEV, "test_runs.md")
    try:
        # ⚠️ 必须 `newline=""` —— 否则 Windows 上 Python 会把 "\n" 转成 "\r\n"，
        #   让这个 LF 文件被追加出**混合换行**（本仓 `dev/*` 约定 LF）。
        with io.open(log, "a", encoding="utf-8", newline="") as f:
            f.write("\n## %s  runner\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("- 环境：%s（%s）\n"
                    % ("／".join(s for s, _ in stages),
                       "本机有代理变量" if has_proxy() else "本机无代理变量"))
            f.write("- 验收级：%s\n"
                    % ("**是**（带代理／无代理双环境）" if both
                       else "否（`--env once` 单环境，**不可作为验收依据**）"))
            f.write("- A1 前端冒烟：%s\n" % ("已启用（`--with-a1`）" if with_a1
                                          else "未启用（默认，8 套口径）"))
            for script, label in SUITES:
                r = results[(stages[-1][0], script)]
                f.write("- `%s` — %s｜断言 %s｜%.1fs\n"
                        % (script, label, r.get("count") or "n/a", r["dt"]))
            # ⚠️ **A1 必须写进日志**（2026-10-08 评估 P1 抓到的真缺陷）：
            #   此前这里**只**遍历 `SUITES`，导致 A1 结果在 `test_runs.md` 里**零痕迹**
            #   —— 而 `all_ok` 是含 A1 的。后果：A1 失败时日志只有「❌ 有失败」＋
            #   8 套全绿明细，**读不出是哪套挂的、也无法归因**；
            #   A1 通过时也读不出「到底跑没跑」。
            #   ⇒ A1 走**独立解释器与独立口径**（`a1_results`），必须单独成行。
            if with_a1:
                # ⚠️ `stages` 是 `[(stage_name, env), ...]` —— 必须取**元组第 0 项**。
                #   直接 `for stage_name in stages` 会把整个元组当key 去查 dict
                #   → `TypeError: unhashable type: 'tuple'`（实测踩过一次：
                #   表现为「过程日志写入失败」，A1 明细那行**整行丢失**）。
                for stage_name, _e in stages:
                    a = a1_results.get(stage_name) or {}
                    f.write("- `smoke_frontend.py`（A1 · %s）— 前端运行时 G1–G6｜断言 %s｜%.1fs\n"
                            % (stage_name, a.get("count") or "n/a", a.get("dt") or 0.0))
            n_bad = len(decl_bad) + len(a1_decl_bad)
            f.write("- 声明校验：%s（常规 %d 处 ＋ A1 %d 处）｜零污染：%s\n"
                    % ("通过" if n_bad == 0 else "**%d 处不符**" % n_bad,
                       len(decl_bad), len(a1_decl_bad),
                       "通过" if not changed else "**%d 个文件被改**" % len(changed)))
            f.write("- 汇总：%d 套 × %d 环境｜累计断言 %d 次\n"
                    % (len(SUITES) + (1 if with_a1 else 0), len(stages), total_n))
            f.write("- **总判定：%s**\n" % ("✅ 全绿" if all_ok else "❌ 有失败"))
    except Exception as e:
        print("  （过程日志写入失败：%s）" % e)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())