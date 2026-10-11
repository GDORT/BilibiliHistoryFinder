# -*- coding: utf-8 -*-
"""M6 追加验证：悬浮面板显隐（UI.Panel ＋ mountDrawer/mountViews 的默认关闭）。

覆盖：
  · Panel 默认关闭（localStorage 无记录 ⇒ isOpen=false）
  · 两个面板挂载后 style.display === "none"（不遮挡页面）
  · 顶栏 toggle 语义：点一次打开、再点一次关闭，状态写回 localStorage
  · 面板内 .bhf-close 关闭并持久化 false（"关不掉的 bug" 回归护栏）
  · 跨刷新：重新挂载后按持久化状态恢复（打开态保持 / 关闭态不弹）
  · 负向护栏：关闭后 __render 不应被触发（关闭不是重渲染）

⚠️ 这里只验结构与逻辑；样式由 runtime.js 注入。
"""
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src", "userscript", "core.js")
PORT = 18947
if not os.path.exists(SRC):
    print("  x 找不到 core.js：", SRC); sys.exit(1)

try:
    from playwright.sync_api import sync_playwright
except Exception as e:  # noqa
    print("  x 需要 playwright：", e); sys.exit(2)

RESULTS = []


def check(ok, label, extra=""):
    RESULTS.append((bool(ok), label, extra))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  ← " + extra) if extra else ""))
    return ok


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"<html><body></body></html>"
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


MOUNT = """(function(){
    const mount = document.body;
    window.__drawer = BHF.UI.mountDrawer({ mount: mount, getRecords: function(){ return []; } });
    window.__views = BHF.UI.mountViews({ mount: mount, getRecords: function(){ return []; } });
  })"""


def main():
    srv = start_server()
    p = sync_playwright().start()
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("http://127.0.0.1:%d/" % PORT)
    page.add_script_tag(path=SRC)
    time.sleep(0.2)
    try:
        page.evaluate("localStorage.clear();")
        page.evaluate(MOUNT)
        time.sleep(0.3)

        # ---- 1. 默认关闭 ----
        check(page.evaluate("BHF.UI.Panel.isOpen('rules') === false"), "Panel：localStorage 为空 ⇒ 默认关闭")
        check(page.evaluate("BHF.UI.Panel.isOpen('views') === false"), "Panel：views 默认关闭")
        check(page.evaluate("document.querySelector('[data-bhf-drawer]').style.display === 'none'"),
              "规则抽屉挂载后即隐藏（不遮挡右侧）")
        check(page.evaluate("document.querySelector('[data-bhf-views]').style.display === 'none'"),
              "分类视图挂载后即隐藏（不遮挡左侧）")

        # ---- 2. toggle 语义 + 持久化 ----
        check(page.evaluate("BHF.UI.Panel.toggle(document.querySelector('[data-bhf-views]'), 'views') === true"),
              "toggle 第一次 ⇒ 打开")
        check(page.evaluate("document.querySelector('[data-bhf-views]').style.display === ''"),
              "打开后 style.display 置空（可见）")
        check(page.evaluate("BHF.UI.Panel.isOpen('views') === true"), "打开状态写入 localStorage")
        check(page.evaluate("BHF.UI.Panel.toggle(document.querySelector('[data-bhf-views]'), 'views') === false"),
              "toggle 第二次 ⇒ 关闭（旧写法恒 true，此处为回归护栏）")
        check(page.evaluate("document.querySelector('[data-bhf-views]').style.display === 'none'"),
              "关闭后隐藏")

        # ---- 3. 关闭按钮 ----
        page.evaluate("BHF.UI.Panel.setOpen('rules', true); BHF.UI.Panel.apply(document.querySelector('[data-bhf-drawer]'), 'rules');")
        check(page.evaluate("document.querySelector('[data-bhf-drawer] .bhf-close') !== null"), "抽屉有关闭按钮")
        check(page.evaluate("document.querySelector('[data-bhf-views] .bhf-close') !== null"), "分类视图有关闭按钮")
        page.evaluate("document.querySelector('[data-bhf-drawer] .bhf-close').click();")
        check(page.evaluate("BHF.UI.Panel.isOpen('rules') === false"), "点关闭 ⇒ 持久化为 false（下次不再弹出）")
        check(page.evaluate("document.querySelector('[data-bhf-drawer]').style.display === 'none'"), "点关闭 ⇒ 抽屉隐藏")

        page.evaluate("BHF.UI.Panel.setOpen('views', true); BHF.UI.Panel.apply(document.querySelector('[data-bhf-views]'), 'views');")
        page.evaluate("document.querySelector('[data-bhf-views] .bhf-close').click();")
        check(page.evaluate("BHF.UI.Panel.isOpen('views') === false"), "视图点关闭 ⇒ 持久化为 false")

        # ---- 4. 跨刷新：重新挂载按状态恢复 ----
        page.evaluate("document.body.innerHTML = '';")
        page.evaluate(MOUNT)
        time.sleep(0.3)
        check(page.evaluate("document.querySelector('[data-bhf-drawer]').style.display === 'none'"),
              "重新挂载后仍保持关闭（刷新不再弹出）")
        check(page.evaluate("document.querySelector('[data-bhf-views]').style.display === 'none'"),
              "重新挂载后视图仍保持关闭")

        # 打开态也要能跨刷新保持
        page.evaluate("BHF.UI.Panel.setOpen('views', true);")
        page.evaluate("document.body.innerHTML = '';")
        page.evaluate(MOUNT)
        time.sleep(0.3)
        check(page.evaluate("document.querySelector('[data-bhf-views]').style.display === ''"),
              "打开态跨刷新保持可见")

        # ---- 5. 负向护栏：关闭不触发 render ----
        page.evaluate("window.__rendered = 0; var vp = document.querySelector('[data-bhf-views]'); var o = vp.__render; vp.__render = function(){ window.__rendered++; return o && o(); };")
        page.evaluate("window.__beforeClose = window.__rendered;")
        page.evaluate("document.querySelector('[data-bhf-views] .bhf-close').click();")
        check(page.evaluate("window.__rendered === window.__beforeClose"), "关闭面板不触发重渲染（负向护栏）",
              "rendered=%s" % page.evaluate("window.__rendered"))

        # ---- 6. 显式 openIfStored:false ⇒ 强制可见（供测试/嵌入场景）----
        page.evaluate("document.body.innerHTML = '';")
        page.evaluate("""(function(){
             window.__d2 = BHF.UI.mountDrawer({ mount: document.body, openIfStored: false });
           })""")
        check(page.evaluate("document.querySelector('[data-bhf-drawer]').style.display === ''"),
              "openIfStored:false ⇒ 无视持久化，强制可见")

        # ---- 7. 顶栏 toggle 回调：仅打开时触发 ----
        page.evaluate("localStorage.clear(); document.body.innerHTML='';")
        page.evaluate(MOUNT)
        page.evaluate("window.__tcb = []; var vp = document.querySelector('[data-bhf-views]'); BHF.UI.Panel.toggle(vp, 'views', function(open){ window.__tcb.push(open); });")
        page.evaluate("BHF.UI.Panel.toggle(document.querySelector('[data-bhf-views]'), 'views', function(open){ window.__tcb.push(open); });")
        check(page.evaluate("window.__tcb.join(',') === 'true,false'"), "toggle 回调按开/关各触发一次", "got=%s" % page.evaluate("window.__tcb.join(',')"))

    finally:
        browser.close(); p.stop(); srv.shutdown()

    print("\n" + "=" * 60)
    fails = [r for r in RESULTS if not r[0]]
    if not fails:
        print("  结果：全部通过（M6 面板显隐 %d 项）" % len(RESULTS))
        return 0
    print("  结果：%d 通过 / %d 失败" % (len(RESULTS) - len(fails), len(fails)))
    for f in fails:
        print("    FAIL:", f[1])
    return 1


if __name__ == "__main__":
    sys.exit(main())
