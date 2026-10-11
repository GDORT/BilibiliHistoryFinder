# -*- coding: utf-8 -*-
"""
探针**机制层**验证 —— 用本地「假 B站」页面验证 hook 装得上、拿得到完整响应、且不破坏页面。

为什么需要它
------------
K5（fetch hook 可靠性）是油猴阶段**唯一可能让整个方案重评**的卡点。
真实验证需要在 bilibili.com/history 上有登录态 —— 那是**用户手动做的事**，
自动化不了（也不该自动化：会消耗真实风控额度）。

但 K5 里**机制那一半**是可以自动验的：
    · fetch / XHR 的 hook 能否在 document-start 装好
    · 能否读到**完整**响应体
    · clone() 读副本后，页面自己还能不能正常拿到响应（**最容易踩的坑**）
    · 解析出的结构是否符合预期（顶层键 / history 子对象）

⇒ 本脚本用 Playwright + 本地 HTTP 服务模拟一个页面，把上面几条做成**可回归断言**。
  剩下的「真实 B站能否拦到」由用户装脚本后手动确认，两边合起来才是完整的 K5 结论。

用法
----
    # 必须用装了 playwright 的那套解释器（见 dev/e2e/env.ps1）
    . dev/e2e/env.ps1
    python dev/userscript/verify_hook.py

    # 或者显式给全环境（bash）
    PYTHONPATH=<repo>/dev/e2e/pkgs \\
    PLAYWRIGHT_BROWSERS_PATH=<repo>/dev/e2e/browsers \\
    NO_PROXY=127.0.0.1,localhost \\
    python dev/userscript/verify_hook.py

退出码：0 = 全过；1 = 有失败。
"""
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # .../BilibiliHistoryFinder
PROBE = os.path.join(ROOT, "dev", "userscript", "probe.user.js")
TARGET = "/x/web-interface/history/cursor"

RESULTS = []


def check(ok, label, extra=""):
    RESULTS.append((bool(ok), label, extra))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  ← " + extra) if extra else ""))
    return ok


# ──────────────────────────── 假 B站 ────────────────────────────
def fake_history_body(n=3, code=0):
    """模拟 history/cursor 响应。

    刻意**照真实结构**构造：顶层**没有** business/oid/bvid（它们在 `history`
    子对象里）—— 这正是原设计稿的关键判断，本脚本会把它变成断言。
    """
    items = []
    for i in range(n):
        items.append({
            "title": "测试标题%d" % i,
            "long_title": "测试标题%d" % i,
            "cover": "http://i0.hdslb.com/cover%d.jpg" % i,
            "uri": "https://www.bilibili.com/video/BV1xx%03d" % i,
            # ⚠️ 关键：oid/bvid/cid/epid/business 全在**子对象**里
            "history": {
                "oid": 1000 + i,
                "bvid": "BV1xx%03d" % i,
                "epid": 0,
                "cid": 2000 + i,
                "page": 1,
                "part": "",
                "business": "",
                "dt": 7,
            },
            "duration": 300 + i * 60,
            "progress": -1 if i % 2 == 0 else 30,
            "view_at": 1760000000 - i * 3600,
            "kid": 3000 + i,
            "videos": 1,
            "author_name": "UP%d" % i,
            "author_mid": 9000 + i,
        })
    return json.dumps({
        "code": code,
        "message": "0",
        "ttl": 1,
        "data": {
            "cursor": {"max": 0, "view_at": 1760000000, "business": "", "ps": 10},
            "list": items,
        },
    }, ensure_ascii=False).encode("utf-8")


PAGE = """<!doctype html><meta charset="utf-8"><title>probe harness</title>
<body>
<div id="out">等待…</div>
<script>
// 页面自己发两种请求：fetch 与 XHR —— 用来检验「B站到底用哪个」这条未知项。
window.__pageGot = { fetch: null, xhr: null };
function boot() {
  // ?fail=101 → 让请求带上 fail=101，服务端会返回业务错误码 -101
  var f = new URLSearchParams(location.search).get('fail');
  var extra = f ? ('&fail=' + f) : '';
  fetch('%(TARGET)s?ps=10&max=0&business=' + extra).then(function (r) {
    return r.json();
  }).then(function (d) {
    window.__pageGot.fetch = (d && d.data && d.data.list) ? d.data.list.length : -1;
    paint();
  }).catch(function (e) { window.__pageGot.fetch = 'ERR:' + e; paint(); });

  var x = new XMLHttpRequest();
  x.open('GET', '%(TARGET)s?ps=10&max=0&business=' + extra, true);
  x.onreadystatechange = function () {
    if (x.readyState === 4) {
      try {
        var d = JSON.parse(x.responseText);
        window.__pageGot.xhr = (d && d.data && d.data.list) ? d.data.list.length : -1;
      } catch (e) { window.__pageGot.xhr = 'ERR:' + e; }
      paint();
    }
  };
  x.send();
}
function paint() {
  document.getElementById('out').textContent =
    '页面正常收到: fetch=' + window.__pageGot.fetch + ' xhr=' + window.__pageGot.xhr;
}
if (document.readyState === 'complete') boot(); else window.addEventListener('load', boot);
</script>
</body>""" % {"TARGET": TARGET}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == TARGET:
            # ?fail=101 → 返回业务错误码 -101（用于验证「未登录」的诊断结论）
            q = self.path.split("?")[1] if "?" in self.path else ""
            body = fake_history_body(3, -101 if "fail=101" in q else 0)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


# ──────────────────────────── 主流程 ────────────────────────────
def main():
    if not os.path.exists(PROBE):
        print("找不到探针脚本: %s" % PROBE)
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 未安装。请用 dev/e2e 那套解释器，或先跑 dev/e2e/setup.ps1")
        print("  PYTHONPATH 应指向 <repo>/dev/e2e/pkgs")
        return 1

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("假 B站 已启动: http://127.0.0.1:%d" % port)
    print()

    probe_src = io.open(PROBE, encoding="utf-8").read()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            # ⭐ 等价于油猴的 @run-at document-start：页面任何脚本之前注入
            page.add_init_script(probe_src)
            page.goto("http://127.0.0.1:%d/" % port, wait_until="load")

            # 等两条都抓到
            deadline = time.time() + 12
            while time.time() < deadline:
                n = page.evaluate("() => (window.__BHF_PROBE__ && window.__BHF_PROBE__.hits.length) || 0")
                if n >= 2:
                    break
                time.sleep(0.2)

            st = page.evaluate("() => window.__BHF_PROBE__")
            page_got = page.evaluate("() => window.__pageGot")

            print("=== 断言 ===")
            hook = (st or {}).get("hook", {})
            hits = (st or {}).get("hits", [])

            check(bool(st), "探针脚本已执行（window.__BHF_PROBE__ 存在）")
            check(hook.get("fetch") is True, "hook 1：window.fetch 已装载")
            check(hook.get("xhr") is True, "hook 2：XMLHttpRequest 已装载")
            check(len(hits) >= 2, "拦到 ≥2 条目标响应（fetch 一条 + xhr 一条）",
                  "实得 %d" % len(hits))

            vias = sorted(set(h.get("via") for h in hits))
            check(vias == ["fetch", "xhr"], "两条 via 分别是 fetch 与 XHR",
                  "实得 %s" % vias)

            h0 = hits[0] if hits else {}
            check(h0.get("status") == 200, "响应状态 200", "实得 %s" % h0.get("status"))
            check((h0.get("bytes") or 0) > 200, "拿到**完整**响应体（非截断）",
                  "bytes=%s" % h0.get("bytes"))
            check(h0.get("code") == 0, "解析出 B站业务码 code=0", "实得 %s" % h0.get("code"))
            check(h0.get("listLen") == 3, "解析出 data.list 长度 = 3",
                  "实得 %s" % h0.get("listLen"))
            check(h0.get("parseError") is None, "JSON 解析无错误",
                  str(h0.get("parseError") or ""))

            # ⭐ 原设计稿的关键判断：顶层**没有** business/oid/bvid，它们在 history 子对象里
            top = h0.get("topKeys") or []
            check("business" not in top, "顶层键**不含** business（印证原设计判断）",
                  "顶层键=%s" % ",".join(top[:12]))
            check(h0.get("hasHistorySub") is True, "存在 history 子对象")
            sub = h0.get("historyKeys") or []
            for k in ("oid", "bvid", "cid"):
                check(k in sub, "history 子对象含 %s（raw 完整性来源）" % k,
                      "子对象键=%s" % ",".join(sub))

            # ⭐ 最容易踩的坑：clone() 读副本后，页面自己还能不能正常用响应
            check(page_got and page_got.get("fetch") == 3,
                  "页面自身 fetch **仍正常**拿到 3 条（clone 未消耗原响应）",
                  "实得 %s" % (page_got or {}).get("fetch"))
            check(page_got and page_got.get("xhr") == 3,
                  "页面自身 XHR **仍正常**拿到 3 条",
                  "实得 %s" % (page_got or {}).get("xhr"))

            # 角标存在（说明 DOM 注入也没问题）
            badge = page.evaluate(
                "() => { var b=document.getElementById('bhf-probe-badge');"
                " return b ? b.textContent : null; }")
            check(badge and "抓到" in badge, "角标已渲染且显示条数", str(badge))

            check(not errors, "无 pageerror（探针未破坏页面）", "; ".join(errors[:2]))

            # ══════════ 诊断引擎（**它自己也要有护栏**）══════════
            # 诊断是给**用户看**的结论：如果它把"没登录"说成"接口变了"，
            # 那比没有诊断更糟。所以每种失败形态都喂一遍，验证结论对得上。
            print()
            print("=== 诊断引擎（人造故障 → 结论必须正确）===")

            def diag(hook, seen_n, hits):
                return page.evaluate(
                    "([h, s, hits]) => window.__BHF_PROBE__.diagnoseWith("
                    "{hook: h, seenTargetRequests: s, hits: hits, seen: []})",
                    [hook, seen_n, hits])

            ok_hook = {"fetch": True, "xhr": True}

            d = diag(ok_hook, 2, [{"code": 0, "parseError": None}])
            check(d["level"] == "ok" and d["failedStep"] is None,
                  "诊断·正常 → 结论通过", d.get("title", ""))

            d = diag({"fetch": False, "xhr": False}, 0, [])
            check(d["failedStep"] == "hook", "诊断·hook 未装 → 定位到 hook 步骤",
                  d.get("title", ""))

            d = diag(ok_hook, 0, [])
            check(d["failedStep"] == "seen", "诊断·没见到请求 → 定位到 seen 步骤",
                  d.get("title", ""))

            d = diag(ok_hook, 3, [])
            check(d["failedStep"] == "capture",
                  "诊断·见到 3 次请求但 0 响应 → 定位到 capture",
                  d.get("title", ""))

            d = diag(ok_hook, 1, [{"code": None, "parseError": "Unexpected token"}])
            check(d["failedStep"] == "parse", "诊断·JSON 解析失败 → 定位到 parse",
                  d.get("title", ""))

            d = diag(ok_hook, 1, [{"code": -101, "parseError": None}])
            check(d["failedStep"] == "code" and "登录" in (d.get("reason") or ""),
                  "诊断·code=-101 → 定位到 code 且说明是登录问题", d.get("reason", ""))

            # ⭐ 端到端：真的让服务端返 -101，看诊断是否走同一条结论
            page2 = browser.new_page()
            page2.add_init_script(probe_src)
            page2.goto("http://127.0.0.1:%d/?fail=101" % port, wait_until="load")
            deadline = time.time() + 12
            while time.time() < deadline:
                n = page2.evaluate(
                    "() => (window.__BHF_PROBE__ && window.__BHF_PROBE__.hits.length) || 0")
                if n >= 2:
                    break
                time.sleep(0.2)
            v2 = page2.evaluate("() => window.__BHF_PROBE__.diagnose().failedStep")
            r2 = page2.evaluate("() => window.__BHF_PROBE__.diagnose().reason")
            check(v2 == "code" and "登录" in (r2 or ""),
                  "诊断·端到端（真返 -101）→ 同样定位到登录问题", "%s / %s" % (v2, r2))
            page2.close()

            # ══════════ 一键导出报告 ══════════
            print()
            print("=== 报告导出 ===")
            rep = page.evaluate("() => window.__BHF_PROBE__.report()")
            check("env" in rep and "url" in (rep.get("env") or {}),
                  "report() 含环境信息（页面 URL / 登录态）")
            check(len(rep.get("steps") or []) == 5, "report() 含 5 个步骤结果",
                  "实得 %d" % len(rep.get("steps") or []))
            check("counts" in rep and "seenSummary" in rep,
                  "report() 含计数与请求去重摘要")

            txt = page.evaluate("() => window.__BHF_PROBE__.reportText()")
            for part in ("# BHF 探针诊断报告", "## 结论", "## 分步结果", "## 计数"):
                check(part in txt, "报告文本含「%s」" % part)

            # 点角标 → 面板弹出且内容非空（用户实际上就是这么用的）
            page.click("#bhf-probe-badge")
            page.wait_for_selector("#bhf-probe-panel textarea", timeout=5000)
            ta = page.eval_on_selector("#bhf-probe-panel textarea", "el => el.value")
            check(len(ta or "") > 200, "点角标后面板弹出且内容非空（可手动复制）",
                  "字符数=%d" % len(ta or ""))

            browser.close()
    finally:
        srv.shutdown()

    npass = sum(1 for ok, _, _ in RESULTS if ok)
    print()
    print("=" * 60)
    print("  结果：%d/%d" % (npass, len(RESULTS)))
    print("=" * 60)
    print()
    print("⚠️ 这**只验证了机制层**。K5 的另一半「真实 bilibili.com/history 上能否拦到」")
    print("   需要你装好 probe.user.js 后手动确认 —— 见 doc/油猴脚本.md。")
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
