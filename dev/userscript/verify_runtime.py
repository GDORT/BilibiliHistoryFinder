# -*- coding: utf-8 -*-
"""M5 集成验证：构建出的 bili_history.user.js 在浏览器里真能启动并工作。

用本地 HTTP 服务起一个带 /x/web-interface/history/cursor 假接口的页面，
把**最终构建产物** bili_history.user.js 注入（模拟油猴在 document-start 后注入），
验证：
  · 启动无 pageerror；window.BHF 与 __BHF_RT__ 注入保护就位
  · fetch 被 hook 包裹（旁听机制存在）
  · 顶栏 / 规则抽屉 / 导出按钮 都装配好
  · 页面 fetch 假接口 → 响应被 clone 旁听 → 入库（Storage 计数增加）
  · 入库后抽屉里规则仍可用、导出可触发（onExport 桩）

⚠️ 真实 B站历史页的 CFG 选择器留 null，本测试只验"抓取+UI"骨架，不验真实 DOM 打标。
"""
import json
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src", "userscript", "core.js")
BUILT = os.path.join(ROOT, "bili_history.user.js")
PORT = 18934
if not os.path.exists(BUILT):
    print("  x 还没构建产物：先跑 build.py"); sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
except Exception as e:  # noqa
    print("  x 需要 playwright：", e); sys.exit(2)

RESULTS = []


def check(ok, label, extra=""):
    RESULTS.append((bool(ok), label, extra))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  ← " + extra) if extra else ""))
    return ok


FAKE = json.dumps({
    "code": 0, "message": "0", "ttl": 1,
    "data": {"cursor": {"max": 1, "view_at": 1, "business": "archive", "ps": 20}, "list": [
        {"kid": "r1", "title": "A", "duration": 100, "progress": 98, "view_at": 1700000000 - 5 * 86400,
         "history": {"bvid": "BV1", "business": "archive"}},
        {"kid": "r2", "title": "B", "duration": 45, "progress": 10, "view_at": 1700000000 - 3 * 86400,
         "history": {"bvid": "BV2", "business": "archive"}},
        {"kid": "r3", "title": "C", "duration": 900, "progress": 300, "view_at": 1700000000 - 2 * 86400,
         "history": {"bvid": "BV3", "business": "live"}}
    ]}
})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/bili_history.user.js"):
            with open(BUILT, "rb") as f: data = f.read()
            self.send_response(200); self.send_header("Content-Type", "application/javascript"); self.end_headers(); self.wfile.write(data)
        elif self.path == "/x/web-interface/history/cursor":
            body = FAKE.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        else:
            body = b"<html><body></body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    srv = start_server()
    p = sync_playwright().start()
    browser = p.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:%d/" % PORT)
    page.add_script_tag(path=BUILT)   # 模拟油猴注入（document-start 后）
    time.sleep(0.3)
    try:
        check(page.evaluate("!!window.BHF") is True, "BHF 已挂载")
        check(page.evaluate("window.__BHF_RT__ === true") is True, "运行时注入保护就位")
        check(len(errors) == 0, "启动无 pageerror", "errs=%s" % errors)

        # fetch 被 hook 包裹（原生 fetch 的 toString 含 [native code]，包裹后不再含）
        wrapped = page.evaluate("window.fetch.toString().indexOf('[native code]') === -1")
        check(wrapped, "fetch 已被 BHF 包裹（非 native，hook 安装）")

        # 顶栏 + 抽屉 + 导出按钮装配
        check(page.evaluate("!!document.getElementById('bhf-topbar')"), "顶栏已装配")
        check(page.evaluate("!!document.querySelector('[data-bhf-drawer]')"), "规则抽屉已装配")
        check(page.evaluate("!!document.querySelector('.bhf-tb-btn[data-act=export]')"), "导出按钮已装配")

        # 旁听入库：fetch 假接口 → clone → ingest → Storage 计数增加
        before = page.evaluate("BHF.Storage.count()")
        page.evaluate("fetch('/x/web-interface/history/cursor').catch(function(){})")
        time.sleep(0.4)
        after = page.evaluate("BHF.Storage.count()")
        check(after >= 3, "旁听入库：fetch 假接口 → Storage 计数增加", "before=%s after=%s" % (before, after))

        # 入库后抽屉规则仍可用、导出不崩
        drawer_ok = page.evaluate(
            """(function(){
                 const ta = document.querySelector('.bhf-rule-json');
                 if (!ta) return {ok:false, why:'no textarea'};
                 const rules = JSON.parse(ta.value);
                 const v = BHF.UI.validateRules(rules);
                 return {ok: Array.isArray(rules.rules) && v.hard.length===0};
               })""")
        check(drawer_ok.get("ok"), "入库后抽屉默认规则仍有效")
        # 导出按钮点击不应抛错（headless 下 download 不影响）
        page.evaluate("document.querySelector('.bhf-tb-btn[data-act=export]').click();")
        time.sleep(0.2)
        check(len(errors) == 0, "导出点击后无 pageerror", "errs=%s" % errors)

    finally:
        browser.close(); p.stop(); srv.shutdown()

    print("\n" + "=" * 60)
    fails = [r for r in RESULTS if not r[0]]
    if not fails:
        print("  结果：全部通过（M5 集成 %d 项）" % len(RESULTS))
        return 0
    print("  结果：%d 通过 / %d 失败" % (len(RESULTS) - len(fails), len(fails)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
