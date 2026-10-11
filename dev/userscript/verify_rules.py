# -*- coding: utf-8 -*-
"""M5 补齐验证：可视化规则编辑器（RuleSet.mountEditor）。

对照 `doc/方案-油猴阶段开发流程.md` §M5 要求：
「单活跃切换 · 新增空白/预设 · 二次调整 · 冲突提示」——原先只有 JSON 文本框，
本套覆盖可视化编辑器，验证：
  · 规则列表渲染 ＋ 单选激活（切换后只有一条 active）
  · 新增空白 / 新增预设
  · 分组与条件的增删（add-group / add-cond / del-group / del-cond）
  · 改字段/运算符/值 → 从 DOM collect 回工作副本（数值字段保持数字型）
  · 保存 → localStorage 持久化 ＋ 触发 onChange
  · 硬冲突（C1）→ 拒绝保存、localStorage 不被污染（负向护栏）
  · 取消 → 回到原规则（负向护栏）
  · setActive：恰好一条 active（不变量）
"""
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src", "userscript", "core.js")
PORT = 18942
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

        # 挂载编辑器（onChange 桩）
        page.evaluate(
            """(function(){
                 window.__changed = 0;
                 const m = document.createElement('div'); document.body.appendChild(m);
                 window.__ed = BHF.RuleSet.mountEditor({
                   mount: m,
                   onChange: function(rules){ window.__changed++; window.__lastRules = JSON.parse(JSON.stringify(rules)); }
                 });
               })""")

        check(page.evaluate("!!document.querySelector('[data-bhf-ruleset]')"), "规则集编辑器已挂载")
        check(page.evaluate("!!document.querySelector('.bhf-rs-list')"), "规则列表存在")
        check(page.evaluate("document.querySelectorAll('.bhf-rs-item').length") == 1, "默认规则 1 条")
        check(page.evaluate("!!document.querySelector('.bhf-rs-g')"), "分组编辑区存在（默认 5 组）")
        check(page.evaluate("document.querySelectorAll('.bhf-rs-g').length") == 5, "默认规则 5 个分组")
        check(page.evaluate("document.querySelector('.bhf-rs-msg').className.indexOf('bhf-msg-ok')>=0"), "默认规则校验 ok")

        # 条件行：字段/运算符/值下拉齐全（默认规则 5 组共 6 条条件：接近看完1+误触2+短视频1+直播1+陈旧1）
        check(page.evaluate("document.querySelectorAll('.bhf-rs-c').length") == 6, "共 6 条条件行",
              "got=%d" % page.evaluate("document.querySelectorAll('.bhf-rs-c').length"))
        check(page.evaluate("!!document.querySelector('.bhf-rs-cf') && !!document.querySelector('.bhf-rs-co') && !!document.querySelector('.bhf-rs-cv')"),
              "条件行含 字段/运算符/值 三个控件")

        # 新增空白规则 → 规则变 2 条
        page.evaluate("document.querySelector('[data-act=\"add-blank\"]').click();")
        time.sleep(0.1)
        check(page.evaluate("document.querySelectorAll('.bhf-rs-item').length") == 2, "新增空白 → 规则变 2 条")
        # 空白规则本身是 1 组 1 条件（但未激活，编辑区仍显示当前激活规则）
        blank_shape = page.evaluate(
            """(function(){
                 const b = BHF.RuleSet.blank();
                 return (b.groups||[]).length + '/' + (b.groups[0].conditions||[]).length;
               })""")
        check(blank_shape == "1/1", "空白规则结构 = 1 组 1 条件", "got=%s" % blank_shape)

        # 新增预设 → 3 条
        page.evaluate("document.querySelector('[data-act=\"add-preset\"]').click();")
        time.sleep(0.1)
        check(page.evaluate("document.querySelectorAll('.bhf-rs-item').length") == 3, "新增预设 → 规则变 3 条")

        # 单活跃切换不变量：恰好一条 active
        act = page.evaluate(
            """(function(){
                 const rs = BHF.Rules.load();
                 return rs.rules.filter(function(r){return r.active;}).length;
               })""")
        check(act == 1, "单活跃不变量：恰好一条 active=true", "got=%d" % act)

        # 新增分组 / 新增条件
        g0 = page.evaluate("document.querySelectorAll('.bhf-rs-g').length")
        page.evaluate("document.querySelector('[data-act=\"add-group\"]').click();")
        time.sleep(0.1)
        g1 = page.evaluate("document.querySelectorAll('.bhf-rs-g').length")
        check(g1 == g0 + 1, "新增分组 → 分组数 +1", "%d→%d" % (g0, g1))
        c0 = page.evaluate("document.querySelectorAll('.bhf-rs-c').length")
        page.evaluate("document.querySelector('.bhf-rs-addc').click();")
        time.sleep(0.1)
        c1 = page.evaluate("document.querySelectorAll('.bhf-rs-c').length")
        check(c1 == c0 + 1, "新增条件 → 条件数 +1", "%d→%d" % (c0, c1))

        # 删除条件 / 删除分组
        page.evaluate("document.querySelector('.bhf-rs-delc').click();")
        time.sleep(0.1)
        check(page.evaluate("document.querySelectorAll('.bhf-rs-c').length") == c1 - 1, "删除条件 → 条件数 -1")
        page.evaluate("document.querySelector('.bhf-rs-delg').click();")
        time.sleep(0.1)
        check(page.evaluate("document.querySelectorAll('.bhf-rs-g').length") == g1 - 1, "删除分组 → 分组数 -1")

        # 数值字段改值 → collect 保持 number（不是字符串）
        page.evaluate(
            """(function(){
                 const rs = window.__ed.getRules();
                 rs.rules[0].groups = [{label:'G',action:'auto_skip',conditions:[{field:'duration',op:'<',value:60}]}];
                 window.__ed.render();
                 const v = document.querySelector('.bhf-rs-cv');
                 v.value = '120';
                 v.dispatchEvent(new Event('change', {bubbles:true}));
               })""")
        page.evaluate("document.querySelector('[data-act=\"save\"]').click();")
        time.sleep(0.15)
        saved = page.evaluate("JSON.parse(localStorage.getItem('bhf_rules')).rules[0].groups[0].conditions[0].value")
        check(saved == 120, "保存后数值字段保持数字型（不是字符串）", "got=%r type=%s" % (saved, type(saved).__name__))
        check(page.evaluate("window.__changed") >= 1, "保存触发 onChange")

        # 取消 → 回到 localStorage 里的规则
        page.evaluate(
            """(function(){
                 const rs = window.__ed.getRules();
                 rs.rules[0].name = '被改坏的名字';
                 window.__ed.render();
                 document.querySelector('[data-act=\"cancel\"]').click();
               })""")
        time.sleep(0.1)
        after_cancel = page.evaluate("JSON.parse(localStorage.getItem('bhf_rules')).rules[0].name")
        check(after_cancel != "被改坏的名字", "取消 → 丢弃未保存的修改", "got=%s" % after_cancel)

        # ── 负向护栏：制造 C1 硬冲突（>=0.9 且 <=0.1 同组）→ 拒绝保存 ──
        page.evaluate(
            """(function(){
                 const rs = window.__ed.getRules();
                 rs.rules[0].groups = [{label:'冲突组',action:'auto_skip',
                   conditions:[{field:'progress_pct',op:'>=',value:0.9},{field:'progress_pct',op:'<=',value:0.1}]}];
                 window.__ed.render();
               })""")
        before = page.evaluate("localStorage.getItem('bhf_rules')")
        msg = page.evaluate(
            """(function(){
                 document.querySelector('[data-act=\"save\"]').click();
                 var m = document.querySelector('.bhf-rs-msg');
                 return {cls: m.className, txt: m.textContent};
               })""")
        after = page.evaluate("localStorage.getItem('bhf_rules')")
        check("bhf-msg-hard" in msg["cls"], "负向：C1 硬冲突 → 消息栏报 hard", "msg=%s" % msg["txt"])
        check(before == after, "负向：C1 硬冲突 → 拒绝保存、localStorage 未被污染",
              "before=%s after=%s" % (bool(before), bool(after)))

        # setActive 单元不变量
        sa = page.evaluate(
            """(function(){
                 const rules = {rules:[{id:'a',active:true},{id:'b',active:false},{id:'c',active:true}]};
                 BHF.RuleSet.setActive(rules, 'b');
                 return rules.rules.map(function(r){return r.id + ':' + r.active;}).join(',');
               })""")
        check(sa == "a:false,b:true,c:false", "setActive：切换后只有目标 active（其余全置 false）", "got=%s" % sa)

    finally:
        browser.close(); p.stop(); srv.shutdown()

    print("\n" + "=" * 60)
    fails = [r for r in RESULTS if not r[0]]
    if not fails:
        print("  结果：全部通过（M5 可视化规则编辑器 %d 项）" % len(RESULTS))
        return 0
    print("  结果：%d 通过 / %d 失败" % (len(RESULTS) - len(fails), len(fails)))
    return 1


if __name__ == "__main__":
    sys.exit(main())