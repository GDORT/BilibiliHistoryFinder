# -*- coding: utf-8 -*-
"""前端运行时冒烟· **MVP-3**（设计稿 `doc/log/2026-10-07-设计-前端运行时自动化测试.md` §十）。

**六组全落地**（设计稿 §六）：

| 组 | 覆盖 | 引入于 |
| --- | --- | --- |
| **G1** 加载完整性 | 页面无 `pageerror`；列表条数 == 夹具 `TOTAL` | MVP-1（MVP-2 补条数断言） |
| **G2** 能力 → DOM 映射 | 置灰集合 == `available=false` ∩ `CAP_DOM` ＋ 独立基线 ＋ `remark` 动态置灰 | MVP-2 |
| **G3** 横幅两档 | `renderSourceBanner` 的 bad / warn / ok ＋ `srcAct` ＋ `dataset.sig` | MVP-1 |
| **G4** 同步四态 | `applySyncStatus` 的 完成 / skip / blocked / running | MVP-1 |
| **G5** 排序联动 ⭐ | **`#42` 行为锁**：升/降序**缺值恒末尾**（已用回退实现验证） | MVP-3 |
| **G6** `duration` 边界 ⭐ | **`#41` 行为锁**：`duration=0`/`NULL` 不被误标「短视频碎片」（同样回退验证过） | MVP-3 |

⭐ **两个行为锁的验证方式**（不是"跑通了"，是"把修复撤掉看会不会红"）：
回退 `#42` → 降序缺值跑到**位置 0–8**（原bug），3 条断言转红；
回退 `#41` → **7 条 `duration=0` 全被误标**（435/462 那个 bug 的最小复现），2 条转红。

**MVP-2 引入的两个前提**：
  1. **夹具库**：`make_fixtures.py` 生成**确定性** 39 条（五分类齐全、`duration` 长尾
     显式覆盖 ZERO/NULL/NORMAL）→ 注入沙箱 `data/bilibili_history.db`。
     ⚠️ **绝不写真实 `data/`** —— 只写沙箱临时目录，跑完`rmtree`。
  2. **判据来自真实产出（P4）**：G2 的期望值**不是硬编码**，而是运行时
     `GET /api/capabilities` 的真实响应里`available=false` 的能力键 → 经页面里的
     **`CAP_DOM`** 表换算出的选择器集合。
     ⚠️ **同源断言会假绿**（实测：删掉 `CAP_DOM.export` 的一个选择器，断言**仍全绿**）
     ⇒ 故有**独立基线** `BASELINE_SELECTORS` 兜底（详见 `feed_caps()` 的 docstring）。

**MVP-3 补的**：G5/G6 需要 `duration` 长尾铺进**各视图**（needs/skipped/stale）
——这是 MVP-2 夹具的延伸，不另造库。

**四条铁律（本仓既有约定，见 `dev/README.md` §二/§三）**：
  1. **拓扑同构** —— 真起 `src/server.py` 子进程、真开 Chromium、真读 DOM，
     **不把渲染逻辑抄进脚本**。G4/G6 直接调页面里的真实函数，走真实链路。
  2. **复制仓库跑真进程** —— `copytree src/` ＋ 夹具 `data/` ＋ 随机端口（复用
     `verify_riskfix.py` 的拓扑），**绝不碰真实 `data/`**。
  3. **断言不藏环境前提** —— 形态（standalone/combined）显式分流，判据只断
     **结构事实**（类名 / hidden / 条数 / `dataset.sig` 档位前缀），**不断文案**。
  4. **反例对照** —— 每条正例都配「不该成立」的一侧，且**该侧必须通过**。

⚠️ **为什么可以直接 `page.evaluate(applySyncStatus, 样本)`**：
   `app.js` 里它是**顶层 `function` 声明**（不是 IIFE 里的局部函数），在页面全局作用域可见。
   已实测确认（`__probe` 组断言「函数可达」），**不是靠假设**。
   同理 `renderSourceBanner(s, raw)` **收两个参数** —— `reachable` 取自 `s.reachable`
   （经`refreshSourceHealth` 折算的 view），`sessdata`/`source` 取自 `raw`。
   ⚠️ 喂样本时**两个参数都要给**，漏掉 `raw` 会让 `sessdata.state` / `source.requested`
   永远取不到 → 横幅档位算错（实测踩过，见 G3 反例）。

用法（**必须先加载 env.ps1**，否则找不到 playwright 与内核）：
    . dev\\e2e\\env.ps1
    & "<python>" dev\\e2e\\smoke_frontend.py
或直接 `python dev\\run_all.py --with-a1`（runner 会自己解析 env.ps1）。
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

# ---- Playwright 必须来自 dev/e2e/pkgs（由 env.ps1 设好 PYTHONPATH）------------
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.stderr.write(
        "[FATAL] 找不到 playwright —— 先加载隔离环境：\n"
        "         . dev\\e2e\\env.ps1\n")
    raise SystemExit(2)

HERE = os.path.dirname(os.path.abspath(__file__))     # .../dev/e2e
ROOT = os.path.dirname(os.path.dirname(HERE))        # .../BilibiliHistoryFinder
SRC = os.path.join(ROOT, "src")

sys.path.insert(0, HERE)                # 为了 import make_fixtures（同目录）
import make_fixtures                     # noqa: E402  夹具生成器（§5）

RESULTS = []


def check(ok, label, extra=""):
    RESULTS.append((bool(ok), label, extra))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  <- " + extra) if extra else ""))
    return bool(ok)


def info(label, extra=""):
    print("       . %s%s" % (label, ("  " + extra) if extra else ""))


# ---------------------------------------------------------------- 沙箱
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def build_sandbox():
    """临时副本：src/ 整目录 + 空 data/ + 指向不存在库的Analyzer 配置。

    **与 `verify_riskfix.build_sandbox()` 同构** —— 同一套安全基线，不新造拓扑。
    """
    root = tempfile.mkdtemp(prefix="bhf_a1_")
    shutil.copytree(SRC, os.path.join(root, "src"))
    os.makedirs(os.path.join(root, "data", "run"), exist_ok=True)
    port = free_port()
    # ⭐ **注入确定性夹具**（MVP-2）。写在 `build_sandbox` 里 → 服务一起来就带上，
    #   不需要额外的「先造库再起服务」编排。**只写沙箱内路径。**
    make_fixtures.make_history_db(os.path.join(root, "data", "bilibili_history.db"))
    with io.open(os.path.join(root, "config.json"), "w", encoding="utf-8") as f:
        f.write('{"web_port": %d, "db_path": "data/bilibili_history.db",'
                ' "sessdata": ""}' % port)
    # 后端指向必然连不上的端口 -> 一切中继优雅降级，**零实网**
    with io.open(os.path.join(root, "data", "fetcher_config.json"), "w",
                 encoding="utf-8") as f:
        f.write('{"base": "http://127.0.0.1:1", "api_key": ""}')
    ghost = os.path.join(root, "data", "__no_such_analyzer__.db")
    with io.open(os.path.join(root, "data", "source_config.json"), "w",
                 encoding="utf-8") as f:
        f.write('{"mode": "auto", "analyzer_db": "%s"}' % ghost.replace("\\", "/"))
    return root, port, ghost


def spawn(root, ghost):
    """真起 `src/server.py`。`ANALYZER_DB` 指向沙箱内不存在的路径 ——
    即便配置解析失败回落默认值，也**绝不会**读到真实 Analyzer 库。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["ANALYZER_DB"] = ghost
    env.pop("BHF_PORT", None)
    # 子进程也要能 import 到前端资源所在目录（server.py 自带 sys.path 处理）
    buf = []

    p = subprocess.Popen(
        [sys.executable, os.path.join(root, "src", "server.py")],
        cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1)

    import threading

    def _reader():
        try:
            for ln in p.stdout:
                buf.append(ln.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()
    return p, buf


def stop(p):
    try:
        p.terminate()
        p.wait(timeout=10)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def wait_up(port, proc, budget=25.0):
    t0 = time.time()
    while time.time() - t0 < budget:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------- 样本
# 四个 `renderSourceBanner(s, raw)` 样本。**字段名取自真实 `/api/capabilities`
# 的响应形状**（遵P4/P5）—— `s` 是折算后的 view，`raw` 是端点原始响应。
#⚠️ 每个样本都**同时**给 s 与 raw，缺一档位就��（实测踩过）。
BANNER_SAMPLES = {
    # bad：Analyzer 不可达（主源离线）-> 唯一值得引导去设置的档
    "bad_unreachable": (
        {"reachable": False, "sessdata": {"state": "unknown"},
         "source": {"effective": "local", "requested": "auto", "local": 0}},
        {"reachable": False, "sessdata": {"state": "unknown"},
         "source": {"effective": "local", "requested": "auto", "local": 0}},
    ),
    # warn：requested=local -> 有意「只用本地库」，不算不可用
    "warn_requested_local": (
        {"reachable": True, "sessdata": {"state": "unknown"},
         "source": {"effective": "local", "requested": "local", "local": 42}},
        {"reachable": True, "sessdata": {"state": "unknown"},
         "source": {"effective": "local", "requested": "local", "local": 42}},
    ),
    # warn：#37 诊断 warn（Analyzer 库里没有记录）
    "warn_diag": (
        {"reachable": True, "sessdata": {"state": "unknown"},
         "source": {"effective": "analyzer", "requested": "auto", "local": 0}},
        {"reachable": True, "sessdata": {"state": "unknown"},
         "source": {"effective": "analyzer", "requested": "auto", "local": 0},
         # #37诊断在 raw.source.diagnosis 里（见 app.js:2320）
         },
    ),
    # ok：一切正常（可达 + 凭证有效 + 无诊断问题 + auto 拿到数据）
    "ok_all_good": (
        {"reachable": True, "sessdata": {"state": "valid"},
         "source": {"effective": "analyzer", "requested": "auto", "local": 0}},
        {"reachable": True, "sessdata": {"state": "valid"},
         "source": {"effective": "analyzer", "requested": "auto", "local": 0}},
    ),
    # bad：凭证失效（-101）—— 与「不可达」同档，但原因不同 -> sig 必须不同
    "bad_sessdata_invalid": (
        {"reachable": True, "sessdata": {"state": "invalid"},
         "source": {"effective": "analyzer", "requested": "auto", "local": 0}},
        {"reachable": True, "sessdata": {"state": "invalid"},
         "source": {"effective": "analyzer", "requested": "auto", "local": 0}},
    ),
}

# `applySyncStatus(s)` 的四态样本（键名取自真实 `/api/sync` 响应）
SYNC_SAMPLES = {
    "ok": {"running": False, "last": {"ok": True, "mode": "incremental",
                                      "fetched": 12}, "meta": {"version": 1}},
    # plan_mode=skip —— **刻意不复用失败样式**（阶段 4 的有意区分，最易被改回红）
    "skip": {"running": False, "last": {"ok": False, "plan_mode": "skip",
                                         "err": "冷却中"}, "meta": {}},
    # plan_mode=blocked —— 凭证闸门 R5，「没开始」要说清原因
    "blocked": {"running": False, "last": {"ok": False, "plan_mode": "blocked",
                                           "err": "凭证无效"}, "meta": {}},
    # running ->进度条可见
    "running": {"running": True, "progress": {"fetched": 30,
                                              "total_estimate": 60, "page": 2,
                                              "mode": "incremental"},
                "meta": {}},
}


def fetch_caps(port):
    """真读 `GET /api/capabilities` —— **P4：判据取自真实产出，不硬编码**。

    ⚠️ 必须走 `ProxyHandler({})` —— 本机 127.0.0.1 走代理会返 502
    （`verify_riskfix.py` 顶部记录过这个坑）。
    """
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    raw = op.open("http://127.0.0.1:%d/api/capabilities" % port, timeout=10).read()
    return json.loads(raw.decode("utf-8"))


def post_json(port, path, body):
    """真发 `POST`（走真实端点，**不绕代理**）。"""
    import urllib.request
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(op.open(req, timeout=20).read().decode("utf-8"))


def read_dom_kids(page):
    """读 DOM 上**实际呈现的卡片顺序**（`li.item` 的 `data-skip` → 顺序即渲染顺序）。

    ⚠️ **刻意不读 duration 文本**（`fmtDur` 输出 `m:ss` / `h:mm:ss`）——
       文案随产品改，是脆弱断言（设计稿 §三 红线）。改为：
       **DOM 顺序的 kid 序列** ＋ **端点响应的 duration** 双向对照。
    """
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#list li.item'))"
        ".map((el, i) => ({i: i, skip: el.dataset.skip || ''}))")


def page_sort_kids(page, direction):
    """驱动页面走**真实排序链路**并读回渲染顺序。

    链路：设 `sortFields` → `load(true)` → 页面发 `POST /api/query` → 后端 `apply_sort`
    → `render()` → DOM。**不抄排序逻辑到测试里**（P1 拓扑同构）。
    ⚠️ 参数名**不能叫 `dir`** —— 它遮蔽内置函数，且我第一次就写成了 `page_sort_kids(page)`
       却按两参调用 → TypeError。
    """
    page.evaluate("""(d) => {
        sortFields = [{ field: 'duration', dir: d }];
        return load(true);      // 真发一次请求
    }""", direction)
    page.wait_for_function("() => !loading", timeout=15000)
    return [x["i"] for x in read_dom_kids(page)]


def dur_sequence(port, view, dir_):
    """端点侧的 duration 序列（真实产出，供与 DOM 顺序对照）。"""
    r = post_json(port, "/api/query",
                  {"view": view, "q": "", "limit": 200, "offset": 0,
                   "filter": None,
                   "sort": [{"field": "duration", "dir": dir_}], "lists": {}})
    return [it.get("duration") for it in (r.get("items") or [])]


def check_missing_tail(durs):
    """返回 (是否恒末尾, 缺值位置列表) —— `#42` 的核心判据。

    ⚠️ **缺值 == `duration is None`**，**不是 `0`**！实测踩过：`sort_value(0)`
    返回 `(0, 0.0)`＝**有值段**；只有 `None` 返回 `(1, 0)`＝缺值段。
    """
    miss = [i for i, d in enumerate(durs) if d is None]
    if not miss:
        return True, []
    return miss == list(range(len(durs) - len(miss), len(durs))), miss


def check_monotonic(durs, reverse):
    """有值段是否单调（升序/降序）。**只看有值段** —— 缺值段不参与。"""
    pres = [d for d in durs if d is not None]
    if reverse:
        return all(pres[i] >= pres[i + 1] for i in range(len(pres) - 1))
    return all(pres[i] <= pres[i + 1] for i in range(len(pres) - 1))


def feed_caps(page, caps, analyzer_ok):
    """把能力表喂给页面里的**真实函数** `applyCapabilities(caps, analyzerOk)`。

    ⚠️ 关于「期望集合怎么来」（2026-10-08 评估后重述，曾是一个死函数留下的教训）：
    `main()` 里的 G2 期望集合是「**从浏览器里读** `CAP_DOM` 换算」而来 ——
    **不是**把这段逻辑抄到 Python 里。这样页面改了表、断言自动跟着改；
    **但风险是「改实现」与「改断言」同步变化 → 一起错 → 假绿**
    （实测：删掉 `CAP_DOM.export` 的一个选择器，同源断言**仍全绿**）。
    ⇒ 故另配**独立基线** `BASELINE_SELECTORS`（脚本内字面清单，与 `app.js` 无关）兜底。
    `remark` 走 `updateRemarkAvailability()` 动态渲染 `.remark-text`（**无 id**），
    不在 `CAP_DOM` 的固定选择器里 → G2 单独计（`G2.4`）。
    """
    page.evaluate("([c, ok]) => applyCapabilities(c, ok)", [caps, analyzer_ok])


def read_gray_state(page):
    """读全文档里 `cap-disabled` 的元素**集合**（选择器 + 元素个数）。"""
    return page.evaluate("""() => {
        const sel = [];
        document.querySelectorAll('.cap-disabled').forEach((el) => {
            if (el.id) sel.push('#' + el.id);
        });
        return {
            ids: sel,
            count: document.querySelectorAll('.cap-disabled').length,
            remark: document.querySelectorAll('.remark-text.cap-disabled').length,
            remarkTotal: document.querySelectorAll('.remark-text').length,
        };
    }""")


def read_cap_dom(page):
    """从浏览器里读 `CAP_DOM`（7 键）与 `UNAVAILABLE_MODE`。"""
    return page.evaluate("""() => ({
        capDom: CAP_DOM,
        mode: UNAVAILABLE_MODE,
        analyzerOnly: ANALYZER_ONLY_DOM,
    })""")


def read_banner(page):
    """读横幅的**结构事实**（不断文案 —— 红线，见设计稿 §三）。"""
    return page.evaluate("""() => {
        const b = document.getElementById('srcBanner');
        const a = document.getElementById('srcAct');
        const d = document.getElementById('srcDot');
        return {
            cls: b ? b.className : null,
            hidden: b ? b.classList.contains('hidden') : null,
            sig: b ? (b.dataset.sig || '') : null,
            actHidden: a ? a.classList.contains('hidden') : null,
            dotCls: d ? d.className : null,
        };
    }""")


def feed_banner(page, key):
    """把样本喂给页面里的**真实函数** `renderSourceBanner(s, raw)`。"""
    s, raw = BANNER_SAMPLES[key]
    return page.evaluate("""([s, raw]) => {
        renderSourceBanner(s, raw);
        const b = document.getElementById('srcBanner');
        const a = document.getElementById('srcAct');
        const d = document.getElementById('srcDot');
        return {
            cls: b ? b.className : null,
            hidden: b ? b.classList.contains('hidden') : null,
            sig: b ? (b.dataset.sig || '') : null,
            actHidden: a ? a.classList.contains('hidden') : null,
            dotCls: d ? d.className : null,
        };
    }""", [s, raw])


def feed_sync(page, key):
    """把四态样本喂给页面里的**真实函数** `applySyncStatus(s)`。

    ⚠️ `ok=true` 分支会调 `pollRuleStatus()`（发一次 fetch）—— 沙箱里端点存在，
    不会报错；这里只读 DOM 同步结果，不等那次fetch。
    """
    return page.evaluate("""(s) => {
        applySyncStatus(s);
        const p = document.getElementById('syncProgress');
        const d = document.getElementById('syncDone');
        const bar = p ? p.querySelector('.sync-bar > i') : null;
        return {
            progressHidden: p ? p.classList.contains('hidden') : null,
            doneCls: d ? d.className : null,
            doneHidden: d ? d.classList.contains('hidden') : null,
            barWidth: bar ? bar.style.width : null,
        };
    }""", SYNC_SAMPLES[key])


# ---------------------------------------------------------------- main
def main():
    print("=" * 68)
    print("  前端运行时冒烟 · MVP-3（G1–G6 全组，含确定性夹具）")
    print("=" * 68)

    info("解释器", sys.version.split()[0])
    info("playwright", os.path.dirname(sys.modules["playwright"].__file__))
    info("浏览器内核", os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "(默认位置)")

    root, port, ghost = build_sandbox()
    proc, buf = spawn(root, ghost)
    base = "http://127.0.0.1:%d" % port
    browser = None
    try:
        if not check(wait_up(port, proc), "沙箱服务已启动", base):
            info("服务未起来，日志尾部：", " | ".join(buf[-6:]))
            return 1
        info("沙箱目录", root)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1400, "height": 900})
            page = ctx.new_page()

            # ---- 错误捕获（G1 的前提）----
            errors, console_errs = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: console_errs.append(m.text)
                    if m.type == "error" else None)

            resp = page.goto(base + "/", wait_until="networkidle", timeout=30000)
            check(resp is not None and resp.ok, "页面加载 HTTP 2xx",
                  "" if resp is None else str(resp.status))

            # ---------------------------------------------------- G1
            print("\n--- G1 加载完整性 ---")
            check(not errors, "G1.1 无 pageerror（JS 运行时错误）",
                  "" if not errors else "捕获 %d 条: %s" % (len(errors), errors[:2]))
            # console.error 可能是可预期的（例如 fetch 失败被catch 后打日志）
            # —— 只做**如实报告**，不作为失败判据（避免断言藏环境前提）。
            info("console.error 条数", str(len(console_errs))
                 + (""if not console_errs else " | " + str(console_errs[:2])))

            # 函数可达性：**MVP-1 的实现前提**，实测确认而非假设
            fn_ok = page.evaluate("""() => typeof applySyncStatus === 'function'
                && typeof renderSourceBanner === 'function'""")
            check(fn_ok, "前置：applySyncStatus / renderSourceBanner 在页面全局可达",
                  "" if fn_ok else "顶层函数不可见 -> 设计稿 §六 G4 的驱动方式需改")

            # ---------------------------------------------------- G1 · 条数
            print("\n--- G1 加载完整性 · 夹具条数（MVP-2）---")
            items = page.evaluate(
                "() => document.querySelectorAll('#list li').length")
            check(items == make_fixtures.TOTAL,
                  "G1.3 列表渲染条数 == 夹具 TOTAL",
                  "实render %d 条，夹具 TOTAL=%d" % (items, make_fixtures.TOTAL))

            # ---------------------------------------------------- G2
            print("\n--- G2 能力 → DOM 映射（applyCapabilities）---")
            caps_doc = fetch_caps(port)
            caps = caps_doc.get("capabilities") or {}
            ana = (caps_doc.get("connection") or {}).get("analyzer") or {}
            dom = read_cap_dom(page)
            cap_dom = dom["capDom"]
            check(dom["mode"] == "gray",
                  "G2.0 前置：UNAVAILABLE_MODE == gray（阶段 4 定稿）",
                  "实际=%s（若已改 hide，G2 的类名判据需同步）" % dom["mode"])
            info("真实响应 available=false 的能力键",
                 ", ".join(sorted(k for k, v in caps.items()
                                  if not v.get("available"))) or "(无)")
            info("CAP_DOM 里非空的键",
                 ", ".join(sorted(k for k, v in cap_dom.items() if v)) or "(全空)")

            # ---- G2.0b **独立基线**：不依赖 CAP_DOM 的选择器清单 -------------
            # ⚠️ **为什么必须要有这一条**（负向验证抓到的真缺陷）：
            #   期望集合若也从浏览器读 `CAP_DOM`，则「改 CAP_DOM」与「改断言」会
            #   **同步变化 → 一起错 → 假绿**。实测：把 `CAP_DOM.export` 删掉
            #   `#anDbBtn`，上面G2.1 仍然全绿。
            #   ⇒ 这里用**脚本内的字面基线**当独立判据：它与 app.js 无关，
            #   改 app.js 的表就会让这条红。**代价**：阶段 4/6 改 UI 时需同步这里
            #   （与设计稿 §八 的「维护成本」是同一笔账，躲不掉）。
            BASELINE_SELECTORS = {
                "fetch": ["#anRealtimeBtn", "#anFullBtn"],
                "export": ["#anExportBtn", "#anDbBtn"],
                "images": ["#anImgBtn", "#anImgStartBtn", "#anImgStopBtn"],
                "integrity": ["#anDataBtn"],
            }
            baseline_ok = True
            for cap, sels in sorted(BASELINE_SELECTORS.items()):
                got = sorted(cap_dom.get(cap) or [])
                if got != sorted(sels):
                    baseline_ok = False
                    info("基线偏差", "%s:期望 %s，实得 %s" % (cap, sorted(sels), got))
            check(baseline_ok,
                  "G2.0b CAP_DOM 基线未被改动（独立于本次断言的字面清单）",
                  "" if baseline_ok else "有偏差 -> 见上方『基线偏差』")

            # ---- G2.0c 夹具自检（确定性 + 长尾覆盖）----
            fx_ok, fx_problems = make_fixtures.selfcheck()
            for label, want, got in fx_problems:
                info("夹具自检偏差", "%s: 期望 %s，实得 %s" % (label, want, got))
            check(fx_ok, "G2.0c 夹具自检通过（条数恒定＋ duration 长尾三段非空）",
                  "" if fx_ok else "见上方『夹具自检偏差』")

            # 期望置灰的选择器集合 = 所有 available=false 的能力键在 CAP_DOM 里的选择器
            expect_sel = []
            for cap, v in caps.items():
                if not v.get("available"):
                    expect_sel.extend(cap_dom.get(cap) or [])
            # `remark` 特殊：CAP_DOM 里是空数组，实际走 `.remark-text` 动态渲染
            remark_expected = 0 if caps.get("remark", {}).get("available") else None

            feed_caps(page, caps, bool(ana.get("ok")))
            g = read_gray_state(page)
            got_sel = sorted(set(g["ids"]))
            check(sorted(expect_sel) == got_sel,
                  "G2.1 置灰元素集合== available=false ∩ CAP_DOM",
                  "期望 %s，实得 %s" % (sorted(expect_sel), got_sel))
            # ⚠️ **不要断言「所有 .cap-disabled 都带 id」** —— 实测 `count=47` 而带 id 的
            #   只有 8 个：差额 39 来自 `.remark-text`（**无 id 的动态元素**，走
            #   `updateRemarkAvailability()`，刻意不在 `CAP_DOM` 的固定选择器里）。
            #   这是**设计如此**，不是「无主置灰」的缺陷（我第一次就误判了，见 §踩坑）。
            #   真正的不变式：置灰总数 == CAP_DOM 置灰 + remark 动态置灰。
            check(g["count"] == g["remarkTotal"] + len(got_sel),
                  "G2.1 置灰总数 == remark 动态置灰 + CAP_DOM 置灰",
                  "count=%d，remark=%d，capdom=%d"
                  % (g["count"], g["remark"], len(got_sel)))

            # 反例：把 `fetch` 谎报成 available=true -> 它的两个按钮必须**解除**置灰
            caps2 = dict(caps)
            caps2["fetch"] = dict(caps.get("fetch") or {}, available=True)
            feed_caps(page, caps2, bool(ana.get("ok")))
            g2 = read_gray_state(page)
            # 期望：把 `fetch` 的选择器从原集合里去掉，其余不变（remark 不受影响）
            fetch_sel = set(cap_dom.get("fetch") or [])
            expect2 = sorted(set(expect_sel) - fetch_sel)
            check(expect2 == sorted(set(g2["ids"])),
                  "G2.2 反例：fetch 谎报可用 -> 其选择器应从置灰集合移除",
                  "期望 %s，实得 %s" % (expect2, sorted(set(g2["ids"]))))
            check(not (fetch_sel & set(g2["ids"])),
                  "G2.2 反例：fetch 的两个按钮此刻必须**不**置灰",
                  "仍置灰：%s" % sorted(fetch_sel & set(g2["ids"])))
            # 还原（后续 G3/G4 不依赖能力表，但保持页面状态干净）
            feed_caps(page, caps, bool(ana.get("ok")))

            # 反例：combined 形态（analyzerOk=true）下 ANALYZER_ONLY_DOM 应被解除
            feed_caps(page, caps, True)
            g3 = read_gray_state(page)
            ao = dom.get("analyzerOnly") or []
            check(all(s not in g3["ids"] for s in ao),
                  "G2.3 反例：analyzerOk=true -> ANALYZER_ONLY_DOM 不应置灰",
                  "ANALYZER_ONLY_DOM=%s，置灰集合=%s" % (ao, sorted(set(g3["ids"]))))
            feed_caps(page, caps, bool(ana.get("ok")))

            if remark_expected is None:
                check(g["remark"] == g["remarkTotal"] and g["remarkTotal"] > 0,
                      "G2.4 remark 不可用 -> 全部 .remark-text 置灰",
                      "置灰 %d / 共 %d" % (g["remark"], g["remarkTotal"]))
            else:
                check(g["remark"] == 0,
                      "G2.4 反例：remark 可用 -> 不得有置灰的 .remark-text",
                      "置灰数=%d" % g["remark"])

            # ---------------------------------------------------- G3
            print("\n--- G3 横幅两档（renderSourceBanner）---")
            r = feed_banner(page, "bad_unreachable")
            check(r["cls"] and "bad" in r["cls"], "G3.1 不可达 -> bad 档",
                  "cls=%s" % r["cls"])
            check(r["actHidden"] is False, "G3.1 反例：bad 档下 srcAct 可见（可引导去设置）",
                  "hidden=%s" % r["actHidden"])
            check(bool(r["sig"]) and r["sig"].startswith("bad:"),
                  "G3.1 dataset.sig 前缀 == 档位", "sig=%r" % (r["sig"] or "")[:60])

            sig_bad_unreach = r["sig"]

            r = feed_banner(page, "warn_requested_local")
            check(r["cls"] and "warn" in r["cls"] and "bad" not in r["cls"],
                  "G3.2 requested=local -> warn 档（不是 bad）", "cls=%s" % r["cls"])
            check(r["actHidden"] is True,
                  "G3.2 反例：warn 档下 srcAct **必须** hidden（对称于 G3.1）",
                  "hidden=%s" % r["actHidden"])
            check(bool(r["sig"]) and r["sig"].startswith("warn:"),
                  "G3.2 sig 前缀 == warn", "sig=%r" % (r["sig"] or "")[:60])

            r = feed_banner(page, "ok_all_good")
            check(r["hidden"] is True, "G3.3 一切正常 -> 横幅 hidden", "")

            # T3-A 教训：同档**同条数但原因不同** -> sig 必须不同
            r2 = feed_banner(page, "bad_sessdata_invalid")
            check(r2["cls"] and "bad" in r2["cls"],
                  "G3.4 凭证失效 -> 同为 bad 档", "cls=%s" % r2["cls"])
            check(r2["sig"] != sig_bad_unreach,
                  "G3.4 反例(T3-A)：同为 bad 档但原因不同 -> sig 必须不同",
                  "两者 sig 相同 = T3-A 坑复发")

            # ---------------------------------------------------- G4
            print("\n--- G4 同步状态四态（applySyncStatus）---")
            r = feed_sync(page, "ok")
            check(r["doneHidden"] is False, "G4.1 ok -> syncDone 可见", "")
            check(r["doneCls"] == "sync-done", "G4.1 类 == sync-done",
                  "cls=%s" % r["doneCls"])
            check("sync-skip" not in (r["doneCls"] or "")
                  and "sync-fail" not in (r["doneCls"] or ""),
                  "G4.1 反例：完成态不得带 skip/fail 类", "cls=%s" % r["doneCls"])

            r = feed_sync(page, "skip")
            check("sync-skip" in (r["doneCls"] or ""),
                  "G4.2 plan_mode=skip -> 带 sync-skip 类", "cls=%s" % r["doneCls"])
            check("sync-fail" not in (r["doneCls"] or ""),
                  "G4.2 反例：**不得**渲染成红色失败（阶段 4 的刻意区分）",
                  "cls=%s" % r["doneCls"])
            check(r["doneHidden"] is False, "G4.2 skip 态可见", "")

            r = feed_sync(page, "blocked")
            check("sync-fail" in (r["doneCls"] or ""),
                  "G4.3 plan_mode=blocked -> 带 sync-fail 类", "cls=%s" % r["doneCls"])

            r = feed_sync(page, "running")
            check(r["progressHidden"] is False, "G4.4 running -> 进度条可见", "")
            check(r["barWidth"] == "50%",
                  "G4.4 进度条宽 == 计算值（30/60 -> 50%）",
                  "width=%s" % r["barWidth"])
            check(r["doneHidden"] is True,
                  "G4.4 反例：running 时 syncDone 必须隐藏", "")

            # G4 结束态：四态跑完不能残留进度条
            r = feed_sync(page, "ok")
            check(r["progressHidden"] is True,
                  "G4.5 反例：从 running 回到 ok 后进度条必须收起",
                  "hidden=%s" % r["progressHidden"])

            # ---- G5 排序联动（`#42` 行为锁）----
            print("\n--- G5 排序联动（#42：缺值恒末尾）---")
            for dir_ in ("asc", "desc"):
                durs = dur_sequence(port, "all", dir_)
                tail_ok, miss = check_missing_tail(durs)
                check(tail_ok,
                      "G5.1 duration/%s -> 缺值恒在末尾" % dir_,
                      "缺值位置 %s（总 %d 条）" % (miss[:6], len(durs)))
                check(check_monotonic(durs, dir_ == "desc"),
                      "G5.1 有值段单调（%s）" % ("降序" if dir_ == "desc" else "升序"),
                      str([d for d in durs if d is not None][:6]))

            # 反例：升降序的**有值段顺序必须不同** —— 否则方向没生效、断言空转
            a = [d for d in dur_sequence(port, "all", "asc") if d is not None]
            b = [d for d in dur_sequence(port, "all", "desc") if d is not None]
            check(a[:5] != b[:5],
                  "G5.2 反例：asc / desc 的有值段顺序必须不同（方向生效）",
                  "asc[:5]=%s desc[:5]=%s" % (a[:5], b[:5]))

            # 反例：#42 的**核心** —— 降序下缺值不得跑到最前（这是修前的 bug）
            d_durs = dur_sequence(port, "all", "desc")
            d_present = [d for d in d_durs if d is not None]
            check(d_durs[0] is not None,
                  "G5.3 反例(#42)：降序时首元素**不得**是缺值",
                  "首元素=%r（修前 `sort_value` 返 (1,0) 会被 reverse 排到最前）" % d_durs[0])
            check(d_durs[-1] is None or not miss,
                  "G5.3 反例(#42)：降序时缺值应集中在末尾",
                  "末元素=%r" % d_durs[-1])
            check(len(d_durs) == make_fixtures.TOTAL,
                  "G5.3 排序不应丢条（升降序都是全量）",
                  "desc 返回 %d 条，夹具 TOTAL=%d" % (len(d_durs), make_fixtures.TOTAL))

            # ---- G5 的 UI 出口：页面真实排序后 DOM 顺序须与端点一致 ----
            for dir_ in ("asc", "desc"):
                n_dom = len(page_sort_kids(page, dir_))
                check(n_dom == make_fixtures.TOTAL,
                      "G5.4 DOM 渲染条数 == 夹具 TOTAL（%s）" % dir_,
                      "DOM %d 条" % n_dom)
            # 反例：切回默认（不排序）后条数不变 —— 排序不该引入/丢失记录
            page.evaluate("() => { sortFields = []; return load(true); }")
            page.wait_for_function("() => !loading", timeout=15000)
            n_plain = len(read_dom_kids(page))
            check(n_plain == make_fixtures.TOTAL,
                  "G5.4 反例：清空排序后条数仍 == TOTAL",
                  "DOM %d 条" % n_plain)

            # ---- G6 duration 边界（`#41` 行为锁，规则层+ UI 出口）----
            print("\n--- G6 duration 边界（#41：零值/缺值不被误判短视频）---")
            fx = post_json(port, "/api/query",
                           {"view": "all", "q": "", "limit": 200, "offset": 0,
                            "filter": None, "sort": [], "lists": {}})
            items = fx.get("items") or []
            zero_items = [it for it in items if it.get("duration") == 0]
            null_items = [it for it in items if it.get("duration") is None]
            check(len(zero_items) > 0,
                  "G6.1 夹具含 duration=0 的记录（#41 的触发样本）",
                  "实得 %d 条" % len(zero_items))
            check(len(null_items) > 0,
                  "G6.1 夹具含 duration=NULL 的记录（#41 的触发样本）",
                  "实得 %d 条" % len(null_items))

            # ⭐ 核心判据：`duration=0` **不得**命中规则 3「短视频碎片」
            bad_zero = [it.get("kid") for it in zero_items
                        if "短视频碎片" in (it.get("auto_skip_reason") or "")]
            check(not bad_zero,
                  "G6.2 #41行为锁：duration=0 **不得**被误标『短视频碎片』",
                  "误标 %d 条: %s" % (len(bad_zero), bad_zero[:3]))
            # 反例：`duration=0` 若命中任何 auto_skip 规则，说明分类有副作用
            check(all(not (it.get("auto_skip_reason") or "").startswith("auto_skip::")
                      for it in zero_items),
                  "G6.2 反例：duration=0 不得命中任何 auto_skip 规则",
                  str([(it.get("kid"), it.get("auto_skip_reason"))
                       for it in zero_items][:3]))
            # 反例：真缺值（NULL）同样不该命中「短视频碎片」
            bad_null = [it.get("kid") for it in null_items
                        if "短视频碎片" in (it.get("auto_skip_reason") or "")]
            check(not bad_null,
                  "G6.3 反例：duration=NULL 也不得被误标『短视频碎片』",
                  "误标 %d 条: %s" % (len(bad_null), bad_null[:3]))
            # 反例：`duration=0` **有值**，排序时属**有值段**、不属缺值段
            asc = dur_sequence(port, "all", "asc")
            check(asc.count(0) == len(zero_items),
                  "G6.4 反例：duration=0 计入**有值段**（sort_value(0) == (0,0.0)）",
                  "asc 里0 的个数=%d，夹具 zero=%d" % (asc.count(0), len(zero_items)))
            #零值应聚在有值段最前（升序）
            first_none = asc.index(None) if None in asc else len(asc)
            check(all(i < first_none for i, d in enumerate(asc) if d == 0),
                  "G6.4 升序时 duration=0 排在缺值段**之前**（它是0，不是缺值）",
                  "首个 None 在第 %d 位" % first_none)

            # ---- 收尾：G1 的错误断言放在最后再查一次 ----
            print("\n--- G1 收尾复查（整个交互过程累计）---")
            check(not errors, "G1.2 整轮交互后仍无 pageerror",
                  "" if not errors else "累计 %d 条: %s" % (len(errors), errors[:3]))
            check(proc.poll() is None, "沙箱子进程仍在跑（未崩溃）")

            if errors:
                page.screenshot(path=os.path.join(root, "fail.png"))
                info("失败截图", os.path.join(root, "fail.png"))

            ctx.close()
        # ⚠️ 不要在这里再 `browser.close()` —— `with sync_playwright()` 退出时
        # 事件循环已关闭，重复关闭抛 "Event loop is closed!"（实测踩过）。
        browser = None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        stop(proc)
        shutil.rmtree(root, ignore_errors=True)

    npass = sum(1 for ok, _, _ in RESULTS if ok)
    #沙箱清理自查：目录必须真消失。⚠️ **不要在这里断言** —— `rmtree` 已执行，但
    # Windows 上目录删除有延迟（实测：内容已空、目录壳仍在），当场 `exists()`
    # 会假失败。改为把结论打印出来，由调用方/日志核对（实测残留目录内容为空）。
    print("       . 沙箱已 rmtree（目录壳可能短暂残留，Windows 删除延迟）")

    print("=" * 68)
    print("结果：%s（%d/%d 项通过）"
          % ("全部通过" if npass == len(RESULTS) else "存在失败项",
             npass, len(RESULTS)))
    print("=" * 68)
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())