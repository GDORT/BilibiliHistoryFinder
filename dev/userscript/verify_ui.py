# -*- coding: utf-8 -*-
"""M4 验证：规则抽屉（UI.mountDrawer）＋ 持久化 ＋ 校验护栏 ＋ 导出。

用本地 HTTP 服务起真实 origin 页面，注入 core.js，验证：
  · 抽屉创建并载入默认规则（textarea 含 rules 且默认规则无 hard）
  · 注入 C1 冲突 JSON → 校验报 hard、保存失败、localStorage 不被污染
  · 合法编辑 → 保存后 localStorage 持久化、校验转 ok
  · 重置 → 回到默认、localStorage 恢复
  · 非法 JSON → 保存失败并提示解析错误（负向护栏）
  · 导出按钮 → 触发 onExport（桩，避免真实下载）

⚠️ 抽屉的视觉样式在 runtime.js 注入，这里只验结构与逻辑（与具体页面解耦）。
"""
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src", "userscript", "core.js")
PORT = 18933
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
        if self.path.endswith("/core.js"):
            with open(SRC, "rb") as f: data = f.read()
            self.send_response(200); self.send_header("Content-Type", "application/javascript"); self.end_headers(); self.wfile.write(data)
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
    page.goto("http://127.0.0.1:%d/" % PORT)
    page.add_script_tag(path=SRC)
    time.sleep(0.2)
    try:
        page.evaluate("localStorage.clear(); BHF.Rules.reset();")

        # 挂载抽屉（onExport 桩）
        page.evaluate(
            """(function(){
                 window.__exported = null;
                 const mount = document.createElement('div'); mount.id='mount'; document.body.appendChild(mount);
                 window.__drawer = BHF.UI.mountDrawer({
                   mount: mount,
                   getRecords: function(){ return [{kid:'x',title:'t'}]; },
                   onExport: function(recs){ window.__exported = recs; }
                 });
               })""")

        check(page.evaluate("!!document.querySelector('[data-bhf-drawer]')"), "抽屉已挂载（data-bhf-drawer 存在）")
        check(page.evaluate("!!document.querySelector('.bhf-rule-json')"), "textarea 存在")
        check(page.evaluate("!!document.querySelector('.bhf-save') && !!document.querySelector('.bhf-reset') && !!document.querySelector('.bhf-export')"), "保存/重置/导出 三个按钮齐全")

        # 默认规则载入且有效
        default_ok = page.evaluate(
            """(function(){
                 const ta = document.querySelector('.bhf-rule-json');
                 const rules = JSON.parse(ta.value);
                 const v = BHF.UI.validateRules(rules);
                 return {hasRules: Array.isArray(rules.rules) && rules.rules.length>0, hardLen: v.hard.length, msgOk: document.querySelector('.bhf-rule-msg').className.indexOf('bhf-msg-ok')>=0};
               })""")
        check(default_ok["hasRules"], "textarea 载入默认规则")
        check(default_ok["hardLen"] == 0 and default_ok["msgOk"], "默认规则校验 ok（无 hard 冲突）")

        # C1 冲突：同组既 >=0.9 又 <=0.1
        conflict_rules = page.evaluate(
            """(function(){
                 const rules = BHF.Engine.default_rules();
                 rules.rules[0].groups.push({label:'冲突组', action:'auto_skip',
                   conditions:[{field:'progress_pct',op:'>=',value:0.9},{field:'progress_pct',op:'<=',value:0.1}]});
                 return JSON.stringify(rules);
               })""")
        page.evaluate("(function(v){ document.querySelector('.bhf-rule-json').value = v; })", conflict_rules)
        before = page.evaluate("localStorage.getItem('bhf_rules')")
        page.evaluate("document.querySelector('.bhf-save').click();")  # 冲突规则保存应失败
        after = page.evaluate("localStorage.getItem('bhf_rules')")
        msg_cls = page.evaluate("document.querySelector('.bhf-rule-msg').className")
        msg_txt = page.evaluate("document.querySelector('.bhf-rule-msg').textContent")
        check("bhf-msg-hard" in msg_cls, "C1 冲突 → 消息报 hard", "msg=%s" % msg_txt)
        check(before == after, "C1 冲突 → 保存失败、localStorage 未被污染", "before=%s after=%s" % (bool(before), bool(after)))

        # 合法编辑：改一个 label 后保存
        page.evaluate(
            """(function(){
                 const rules = BHF.Engine.default_rules();
                 rules.rules[0].groups[0].label = '改过的标签';
                 document.querySelector('.bhf-rule-json').value = JSON.stringify(rules);
                 document.querySelector('.bhf-save').click();
               })""")
        persisted = page.evaluate("JSON.parse(localStorage.getItem('bhf_rules'))")
        check(persisted and persisted["rules"][0]["groups"][0]["label"] == "改过的标签", "合法编辑 → 保存后 localStorage 持久化", "got=%s" % (persisted and persisted["rules"][0]["groups"][0]["label"]))
        check(page.evaluate("document.querySelector('.bhf-rule-msg').className.indexOf('bhf-msg-ok')>=0"), "保存后消息转 ok")

        # 重置 → 恢复默认
        page.evaluate("document.querySelector('.bhf-reset').click();")
        reset_label = page.evaluate("JSON.parse(document.querySelector('.bhf-rule-json').value).rules[0].groups[0].label")
        check(reset_label == "接近看完", "重置 → 回到默认规则", "got=%s" % reset_label)
        reset_persist = page.evaluate("JSON.parse(localStorage.getItem('bhf_rules')).rules[0].groups[0].label")
        check(reset_persist == "接近看完", "重置 → localStorage 也恢复默认")

        # 负向：非法 JSON → 保存失败、提示解析错误
        page.evaluate("document.querySelector('.bhf-rule-json').value = '{这不是合法json';")
        page.evaluate("document.querySelector('.bhf-save').click();")
        bad_cls = page.evaluate("document.querySelector('.bhf-rule-msg').className")
        bad_txt = page.evaluate("document.querySelector('.bhf-rule-msg').textContent")
        check("bhf-msg-hard" in bad_cls and "解析" in bad_txt, "负向：非法 JSON → 保存失败并提示解析错误", "msg=%s" % bad_txt)

        # 导出按钮 → 触发 onExport（桩）
        page.evaluate("document.querySelector('.bhf-export').click();")
        exported = page.evaluate("window.__exported")
        check(exported is not None and len(exported) == 1 and exported[0]["kid"] == "x", "导出按钮 → 触发 onExport 并传出记录", "got=%s" % exported)

    finally:
        browser.close(); p.stop(); srv.shutdown()

    print("\n" + "=" * 60)
    fails = [r for r in RESULTS if not r[0]]
    if not fails:
        print("  结果：全部通过（M4 规则抽屉 %d 项）" % len(RESULTS))
        return 0
    print("  结果：%d 通过 / %d 失败" % (len(RESULTS) - len(fails), len(fails)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
