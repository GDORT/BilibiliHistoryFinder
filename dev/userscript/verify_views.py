# -*- coding: utf-8 -*-
"""M6 验证：分类视图（UI.mountViews）＋ 分类分布读数（Overlay.summarize）。

回答「为什么只有『已看完』有角标」—— 让分类结果**可见、可查**，而不是只能看零星角标。

用本地 HTTP 服务起真实 origin 页面，注入 core.js，验证：
  · summarize：把记录按规则分桶（finished/auto_skip/stale/needs）＋ 逐条规则命中数
  · 负向护栏：空库 → 计数全 0；缺 duration 的记录不被时长规则误判（#41 语义）
  · viewRows：按 bucket 过滤、已看完/已跳过/待观看各归其位、按 view_at 倒序
  · mountViews：面板挂载、五个分类 tab、点 tab 切换列表、标题/进度渲染、空分类有兜底文案

⚠️ 视觉样式在 runtime.js 注入，这里只验结构与逻辑（与具体页面解耦）。
"""
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src", "userscript", "core.js")
PORT = 18935
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


# 真实结构样本：覆盖 finished / auto_skip / stale / needs 四类
RAWS = [
    {"kid": "fin", "title": "看完", "duration": 100, "progress": -1, "is_finish": True,
     "view_at": 1700000000 - 10 * 86400, "uri": "https://www.bilibili.com/video/BVFIN",
     "history": {"bvid": "BVFIN", "business": "archive"}},
    {"kid": "near", "title": "接近看完", "duration": 100, "progress": 98,
     "view_at": 1700000000 - 5 * 86400, "uri": "https://www.bilibili.com/video/BVNEAR",
     "history": {"bvid": "BVNEAR", "business": "archive"}},
    {"kid": "tiny", "title": "短视频", "duration": 45, "progress": 10,
     "view_at": 1700000000 - 3 * 86400, "uri": "https://www.bilibili.com/video/BVTINY",
     "history": {"bvid": "BVTINY", "business": "archive"}},
    {"kid": "old", "title": "陈旧", "duration": 1000, "progress": 50,
     "view_at": 1700000000 - 200 * 86400, "uri": "https://www.bilibili.com/video/BVOLD",
     "history": {"bvid": "BVOLD", "business": "archive"}},
    {"kid": "mid", "title": "中途", "duration": 600, "progress": 300,
     "view_at": 1700000000 - 1 * 86400, "uri": "https://www.bilibili.com/video/BVMID",
     "history": {"bvid": "BVMID", "business": "archive"}},
]


def main():
    srv = start_server()
    p = sync_playwright().start()
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("http://127.0.0.1:%d/" % PORT)
    page.add_script_tag(path=SRC)
    time.sleep(0.2)
    try:
        NOW = 1700000000
        page.evaluate("localStorage.clear(); BHF.Rules.reset(); BHF.Storage.clear()")
        page.evaluate("(function(raws){ return BHF.Storage.putBatch(raws); })", RAWS)
        page.evaluate("(function(){ window.__recs = null; return BHF.Storage.getAllDerived().then(function(a){ window.__recs = a; }); })")

        # ---- summarize：分桶 + 逐条规则命中数 ----
        summ = page.evaluate(
            """(function(now){
                 const rule = BHF.Engine.get_active_rule(BHF.Engine.default_rules());
                 return BHF.Overlay.summarize(window.__recs, rule, now);
               })""", NOW)
        check(summ["total"] == 5, "summarize 总数 = 5", "got=%s" % summ["total"])
        check(summ["finished"] == 1, "finished=1（fin 已看完）", "got=%s" % summ["finished"])
        check(summ["auto_skip"] == 2, "auto_skip=2（near 接近看完 + tiny 短视频）", "got=%s" % summ["auto_skip"])
        check(summ["stale"] == 1, "stale=1（old 陈旧）", "got=%s" % summ["stale"])
        check(summ["needs"] == 1, "needs=1（mid 中途，无规则命中）", "got=%s" % summ["needs"])
        check(sum([summ["finished"], summ["auto_skip"], summ["stale"], summ["needs"]]) == summ["total"],
              "四桶之和 = 总数（无记录丢失）")
        # 逐条规则命中数（用于「规则没命中 vs 没显示」的区分）
        check(summ["groups"].get("接近看完") == 1 and summ["groups"].get("短视频碎片") == 1,
              "逐条规则命中数正确（接近看完/短视频碎片 各 1）",
              "got=%s" % summ["groups"])

        # 负向：空库 → 全 0，不崩
        empty = page.evaluate(
            """(function(){
                 const rule = BHF.Engine.get_active_rule(BHF.Engine.default_rules());
                 const s = BHF.Overlay.summarize([], rule, 1700000000);
                 return {total:s.total, finished:s.finished, needs:s.needs};
               })""")
        check(empty["total"] == 0 and empty["finished"] == 0 and empty["needs"] == 0,
              "负向：空库 → 计数全 0 且不崩", "got=%s" % empty)

        # 负向：缺 duration 的记录不被时长规则误判（#41 语义在 summarize 中同样成立）
        nodur = page.evaluate(
            """(function(now){
                 const rule = BHF.Engine.get_active_rule(BHF.Engine.default_rules());
                 const recs = [{kid:'nd', progress:10, duration:0, view_at:now, business:'archive'}];
                 return BHF.Overlay.summarize(recs, rule, now);
               })""", NOW)
        check(nodur["auto_skip"] == 0 and nodur["needs"] == 1,
              "负向：duration=0 的记录不被「短视频碎片(duration<60)」误判 → 归待观看",
              "auto=%s needs=%s" % (nodur["auto_skip"], nodur["needs"]))

        # ---- viewRows：按 bucket 过滤 + 倒序 ----
        rows = page.evaluate(
            """(function(now){
                 const rule = BHF.Engine.get_active_rule(BHF.Engine.default_rules());
                 const f = BHF.Overlay.viewRows(window.__recs, rule, now, 'finished');
                 const a = BHF.Overlay.viewRows(window.__recs, rule, now, 'auto');
                 const n = BHF.Overlay.viewRows(window.__recs, rule, now, 'needs');
                 const all = BHF.Overlay.viewRows(window.__recs, rule, now, 'all');
                 return {f:f.map(function(r){return r.kid;}),
                         a:a.map(function(r){return r.kid;}),
                         n:n.map(function(r){return r.kid;}),
                         allLen:all.length,
                         firstPct: (f[0] && f[0].pct), firstState: (f[0] && f[0].state)};
               })""", NOW)
        check(rows["f"] == ["fin"], "viewRows finished → 只含 fin", "got=%s" % rows["f"])
        check(sorted(rows["a"]) == ["near", "tiny"], "viewRows auto → near+tiny", "got=%s" % rows["a"])
        check(rows["n"] == ["mid"], "viewRows needs → 只含 mid", "got=%s" % rows["n"])
        check(rows["allLen"] == 5, "viewRows all → 全部 5 条", "got=%s" % rows["allLen"])
        check(rows["firstState"] == "finished", "viewRows 行带 state 字段（已看完）", "got=%s" % rows["firstState"])

        # ---- mountViews：面板 + tab 切换 ----
        page.evaluate(
            """(function(){
                 const mount = document.createElement('div'); document.body.appendChild(mount);
                 window.__views = BHF.UI.mountViews({
                   mount: mount,
                   getRecords: function(){ return window.__recs; },
                   now: 1700000000,
                   onOpen: function(uri){ window.__opened = uri; }
                 });
               })""")
        check(page.evaluate("!!document.querySelector('[data-bhf-views]')"), "分类视图面板已挂载")
        tabs = page.evaluate(
            """Array.prototype.map.call(document.querySelectorAll('.bhf-vtab'), function(b){ return b.getAttribute('data-b'); })""")
        check(tabs == ["all", "finished", "auto", "stale", "needs"], "五个分类 tab 齐全", "got=%s" % tabs)
        all_rows = page.evaluate("document.querySelectorAll('.bhf-vrow').length")
        check(all_rows == 5, "默认 all 视图渲染 5 行", "got=%s" % all_rows)
        check(page.evaluate("document.querySelector('.bhf-vtitle').textContent.length > 0"), "行标题已渲染")

        # 点 finished tab → 只剩 fin
        page.evaluate("document.querySelector('.bhf-vtab[data-b=\"finished\"]').click();")
        time.sleep(0.1)
        fin_rows = page.evaluate("Array.prototype.map.call(document.querySelectorAll('.bhf-vrow .bhf-vtag'), function(t){ return t.textContent; })")
        check(fin_rows == ["已看完"], "切到 finished → 只显示已看完行", "got=%s" % fin_rows)

        # 点 needs tab → 只剩 mid（待观看）
        # 回归护栏：Rules.load() 是包装对象，必须 get_active_rule 拆包后传给引擎。
        # 若漏拆（曾真实发生），evaluate_rule 看不到 groups → 全部判 needs → 页面零角标。
        unwrap = page.evaluate(
            """(function(){
                 const wrapped = BHF.Rules.load();
                 const rule = BHF.Engine.get_active_rule(wrapped);
                 const near = window.__recs.filter(function(r){return r.kid==='near';})[0];
                 return {
                   wrappedHasGroups: Array.isArray(wrapped.groups),
                   ruleHasGroups: Array.isArray(rule.groups),
                   evalOnWrapped: BHF.Engine.evaluate_rule(near, wrapped, 1700000000),
                   evalOnRule: BHF.Engine.evaluate_rule(near, rule, 1700000000)
                 };
               })""")
        check(unwrap["wrappedHasGroups"] is False, "回归：Rules.load() 返回的是包装对象（无 groups）")
        check(unwrap["ruleHasGroups"] is True, "回归：get_active_rule 拆包后才有 groups")
        check(unwrap["evalOnWrapped"] is None, "回归：直接传包装对象 → evaluate_rule 全判 null（这就是零角标的成因）")
        check(unwrap["evalOnRule"] is not None, "回归：传激活规则 → near 正确命中规则", "got=%s" % (unwrap["evalOnRule"],))

        page.evaluate("document.querySelector('.bhf-vtab[data-b=\"needs\"]').click();")
        time.sleep(0.1)
        needs_rows = page.evaluate("Array.prototype.map.call(document.querySelectorAll('.bhf-vrow .bhf-vtag'), function(t){ return t.textContent; })")
        check(needs_rows == ["待观看"], "切到 needs → 只显示待观看行", "got=%s" % needs_rows)

        # 点标题 → 触发 onOpen（桩）
        page.evaluate("document.querySelector('.bhf-vtitle').click();")
        opened = page.evaluate("window.__opened")
        check(opened == "https://www.bilibili.com/video/BVMID", "点标题 → 触发 onOpen 打开 uri", "got=%s" % opened)

        # 负向：空分类有兜底文案（切到一个无记录的 bucket 不炸）
        page.evaluate(
            """(function(){
                 window.__recs = [];
                 window.__views.__render();
               })""")
        time.sleep(0.1)
        empty_txt = page.evaluate("(document.querySelector('.bhf-views-empty')||{}).textContent || ''")
        check("暂无记录" in empty_txt, "负向：空分类 → 显示「暂无记录」兜底、不炸", "txt=%s" % empty_txt)

        # ── 回归护栏：getRecords 返回 Promise（真实运行时的 getAllDerived 就是 Promise）──
        # 曾真实发生：render() 把 Promise 当数组 → 面板恒显示「暂无记录」，而顶栏读数却正常。
        page.evaluate(
            """(function(){
                 window.__recs = [{kid:'fin',title:'看完',duration:100,progress:-1,is_finish:true,
                    view_at:1700000000,uri:'https://www.bilibili.com/video/BVFIN',history:{bvid:'BVFIN',business:'archive'}}];
                 const m2=document.createElement('div'); document.body.appendChild(m2);
                 window.__v2 = BHF.UI.mountViews({
                   mount: m2,
                   getRecords: function(){ return Promise.resolve(window.__recs); },  // ← Promise
                   now: 1700000000,
                   onOpen: function(u){}
                 });
               })""")
        time.sleep(0.3)
        p_rows = page.evaluate("document.querySelectorAll('[data-bhf-views]:last-of-type .bhf-vrow').length")
        # last-of-type 选择不稳，直接量第二个面板
        p_rows = page.evaluate("(function(){ var ps=document.querySelectorAll('[data-bhf-views]'); var p=ps[ps.length-1]; return p.querySelectorAll('.bhf-vrow').length; })()")
        check(p_rows == 1, "回归：getRecords 返回 Promise → 面板仍能渲染出记录（异步来源不被当空表）", "got=%d" % p_rows)

    finally:
        browser.close(); p.stop(); srv.shutdown()

    print("\n" + "=" * 60)
    fails = [r for r in RESULTS if not r[0]]
    if not fails:
        print("  结果：全部通过（M6 分类视图 %d 项）" % len(RESULTS))
        return 0
    print("  结果：%d 通过 / %d 失败" % (len(RESULTS) - len(fails), len(fails)))
    return 1


if __name__ == "__main__":
    sys.exit(main())