# -*- coding: utf-8 -*-
"""M3 验证：overlay 注入（扫描打标 ＋ MutationObserver 自动补标 ＋ cancel 覆盖）。

用本地 HTTP 服务起一个真实 origin 的页面（IndexedDB / localStorage 都需要 origin），
把 core.js 注入后，用**自建 mock SPA**（容器 + 卡片 + data-kid）验证：
  · scan：每张卡片按分类结果打正确角标（finished/auto/stale/needs）
  · 负向护栏：卡片缺 kid / 记录在库中没有 → 不打角标、不崩
  · observe：SPA 滚动分页新增卡片，MutationObserver 防抖后自动补标
  · cancel：点击 cancelable 角标写入覆盖 → 重扫变"已恢复"；再点恢复规则判定

⚠️ 真实 B站历史页的卡片选择器 / kid 属性名留待用户装上去定（runtime.js 里可配）；
   这里只验**注入逻辑本身**是否健壮，与具体页面 DOM 解耦。
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
PORT = 18932
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
            body = b"<html><body><div id='feed'></div></body></html>"
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
        NOW = 1700000000
        DAY = 86400
        # 真实结构样本：顶层 + history 子对象；kid 全用字符串（与 getAttribute 一致）
        raws = [
            {"kid": "f1", "title": "看完", "duration": 100, "progress": -1, "is_finish": True, "view_at": NOW - 10 * DAY,
             "history": {"bvid": "BVF", "business": "archive"}},
            {"kid": "a1", "title": "接近看完", "duration": 100, "progress": 98, "view_at": NOW - 5 * DAY,
             "history": {"bvid": "BVA", "business": "archive"}},
            {"kid": "s1", "title": "短视频", "duration": 45, "progress": 10, "view_at": NOW - 3 * DAY,
             "history": {"bvid": "BVS", "business": "archive"}},
            {"kid": "l1", "title": "直播回放", "duration": 900, "progress": 300, "view_at": NOW - 2 * DAY,
             "history": {"bvid": "BVL", "business": "live"}},
            {"kid": "st1", "title": "陈旧", "duration": 1000, "progress": 50, "view_at": NOW - 200 * DAY,
             "history": {"bvid": "BVST", "business": "archive"}},
            {"kid": "nd1", "title": "需要观看", "duration": 300, "progress": 50, "view_at": NOW - 1 * DAY,
             "history": {"bvid": "BVN", "business": "archive"}},
            {"kid": "a2", "title": "接近看完2", "duration": 100, "progress": 99, "view_at": NOW - 4 * DAY,
             "history": {"bvid": "BVA2", "business": "archive"}},
        ]

        # 预建 mock 卡片 + 灌库
        setup = page.evaluate(
            """(function(raws){
                 const S = window.BHF.Storage;
                 const feed = document.getElementById('feed');
                 function card(kid, withAttr){
                   const d = document.createElement('div');
                   d.className = 'bili-card';
                   if (withAttr !== false) d.setAttribute('data-kid', kid);
                   feed.appendChild(d);
                   return d;
                 }
                 // 初始卡片（含两条负向护栏：缺 kid、库里没有的 kid）
                 ['f1','a1','s1','l1','st1','nd1','nope'].forEach(function(k){ card(k); });
                 card('noattr', false);   // 没有 data-kid 属性的卡片
                 return S.clear().then(function(){ return S.putBatch(raws); });
               })""",
            raws)
        time.sleep(0.1)

        rule = page.evaluate("BHF.Engine.default_rules().rules[0]")
        # 构建 kid→derived 映射（模拟运行时从 IndexedDB 读出）
        records = page.evaluate(
            """(function(){
                 return BHF.Storage.getAllDerived().then(function(all){
                   const m = {}; all.forEach(function(d){ m[d.kid] = d; }); return m;
                 });
               })""")

        # 在页面内执行扫描（container 是 DOM，必须就地构造，不能跨上下文传）
        res = page.evaluate(
            """(function(arg){
                 const feed = document.getElementById('feed');
                 const o = {container: feed, cardSel:'.bili-card', kidAttr:'data-kid',
                            records: arg.records, rule: arg.rule, now: arg.now};
                 window.__scanOpts = o;
                 return BHF.Overlay.scan(o);
               })""",
            {"records": records, "rule": rule, "now": NOW})

        # res 只含"实际放置了角标"的卡片（needs 且无覆盖 → 不打角标，不计入）
        check(len(res) == 5, "scan 给 5 张有效卡片打标（f1/a1/s1/l1/st1；nd1 需观看无角标、nope/noattr 护栏跳过）", "got=%d" % len(res))
        # 逐卡片角标断言
        def badge_of(kid):
            return page.evaluate(
                """(function(kid){
                     const c = document.querySelector('.bili-card[data-kid=\"'+kid+'\"]');
                     const b = c && c.querySelector('.bhf-badge');
                     return b ? b.className : null;
                   })""", kid)
        check(badge_of("f1") == "bhf-badge bhf-done", "f1 已看完 → bhf-done", "got=%s" % badge_of("f1"))
        check(badge_of("a1") == "bhf-badge bhf-auto cancelable", "a1 接近看完 → bhf-auto", "got=%s" % badge_of("a1"))
        check(badge_of("s1") == "bhf-badge bhf-auto cancelable", "s1 短视频 → bhf-auto", "got=%s" % badge_of("s1"))
        check(badge_of("l1") == "bhf-badge bhf-auto cancelable", "l1 直播回放 → bhf-auto", "got=%s" % badge_of("l1"))
        check(badge_of("st1") == "bhf-badge bhf-stale cancelable", "st1 陈旧 → bhf-stale", "got=%s" % badge_of("st1"))
        check(badge_of("nd1") is None, "nd1 需要观看 → 无角标", "got=%s" % badge_of("nd1"))

        # 负向护栏：缺 kid / 库里没有的 kid → 无角标
        check(badge_of("nope") is None, "负向：库里没有的 kid(nope) 不打角标", "got=%s" % badge_of("nope"))
        check(badge_of("noattr") is None, "负向：无 data-kid 属性的卡片不打角标")

        # observe：先挂观察器，再新增卡片（SPA 滚动分页），MutationObserver 应自动补标
        # 注意：records 已由初始 getAllDerived 含 a2（同属 raws），无需补。
        page.evaluate("BHF.Overlay.observe(window.__scanOpts)")
        page.evaluate(
            """(function(){
                 const feed = document.getElementById('feed');
                 function add(kid){ const d=document.createElement('div'); d.className='bili-card'; d.setAttribute('data-kid',kid); feed.appendChild(d); }
                 add('a2');                       // 接近看完 → 应自动打 bhf-auto
                 add('nd2');                       // 库里没有 → 不打
               })""")
        page.wait_for_timeout(200)
        check(badge_of("a2") == "bhf-badge bhf-auto cancelable", "observe：新增 a2 自动补标 bhf-auto", "got=%s" % badge_of("a2"))
        check(badge_of("nd2") is None, "observe：库里没有的 nd2 仍不打角标")

        # cancel：点击 a1 角标 → 覆盖 → 重扫变"已恢复"
        page.evaluate(
            """(function(){
                 const o = window.__scanOpts;
                 o.onChange = function(){ BHF.Overlay.scan(o); };
                 const c = document.querySelector('.bili-card[data-kid=\"a1\"]');
                 const b = c.querySelector('.bhf-badge.cancelable');
                 b.click();
               })""")
        time.sleep(0.15)
        ov = page.evaluate("JSON.parse(localStorage.getItem('bhf_overrides')||'{}')")
        check(ov.get("a1") is True, "cancel：a1 写入覆盖 localStorage", "got=%s" % ov)
        check(badge_of("a1") == "bhf-badge bhf-recovered recoverable", "cancel：a1 重扫变 已恢复(bhf-recovered)", "got=%s" % badge_of("a1"))
        # 再点一次 → 恢复规则判定
        page.evaluate(
            """(function(){
                 const o = window.__scanOpts;
                 const c = document.querySelector('.bili-card[data-kid=\"a1\"]');
                 const b = c.querySelector('.bhf-badge.recoverable');
                 b.click();
               })""")
        time.sleep(0.15)
        ov2 = page.evaluate("JSON.parse(localStorage.getItem('bhf_overrides')||'{}')")
        check("a1" not in ov2, "cancel：再点一次撤销覆盖", "got=%s" % ov2)
        check(badge_of("a1") == "bhf-badge bhf-auto cancelable", "cancel：a1 恢复为 bhf-auto", "got=%s" % badge_of("a1"))

        # ── resolver（真实页卡片无 kid 属性 → 按 uri/path/bvid 反查）──
        page.evaluate(
            """(function(){
                 const S = window.BHF.Storage, NOW = 1700000000;
                 return S.putBatch([
                   {kid:'dupNew', title:'dup新', duration:100, progress:98, view_at:NOW-10,
                    uri:'https://www.bilibili.com/video/BVDUP', history:{bvid:'BVDUP', business:'archive'}},
                   {kid:'dupOld', title:'dup旧', duration:100, progress:98, view_at:NOW-5000,
                    uri:'https://www.bilibili.com/video/BVDUP', history:{bvid:'BVDUP', business:'archive'}},
                   {kid:'liveK', title:'直播', duration:900, progress:300, view_at:NOW-20,
                    uri:'https://live.bilibili.com/777', history:{business:'live'}}
                 ]);
               })""")
        records2 = page.evaluate(
            """(function(){ return BHF.Storage.getAllDerived().then(function(all){
                   const m={}; all.forEach(function(d){ m[d.kid]=d; }); return m; }); })""")

        # 5 张卡：协议相对/带查询串的同 uri ×2、直播(无 bvid)、未知视频(先作者链接)、无链接
        scan2 = page.evaluate(
            """(function(arg){
                 let feed=document.getElementById('feed2');
                 if(!feed){ feed=document.createElement('div'); feed.id='feed2'; document.body.appendChild(feed); }
                 feed.innerHTML='';
                 function c(html){ const d=document.createElement('div'); d.className='rcard'; d.innerHTML=html; feed.appendChild(d); }
                 c('<a href="//www.bilibili.com/video/BVDUP?spm_id_from=333.1">x</a>');
                 c('<a href="https://www.bilibili.com/video/BVDUP">x</a>');
                 c('<a href="https://live.bilibili.com/777">x</a>');
                 c('<a href="https://space.bilibili.com/999">作者</a><a href="https://www.bilibili.com/video/BVZZZZ">y</a>');
                 c('无链接');
                 const o={container:feed, cardSel:'.rcard', resolve:BHF.Overlay.buildResolver(arg.records), records:arg.records, rule:arg.rule, now:arg.now};
                 return BHF.Overlay.scan(o).map(function(x){return x.kid;});
               })""",
            {"records": records2, "rule": rule, "now": NOW})
        check(scan2 == ["dupNew", "dupOld", "liveK"],
              "resolver：uri 归一化(协议/www/查询串) ＋ 重复 uri 按 view_at 倒序消费 ＋ 直播(无bvid)按 uri 命中",
              "got=%s" % scan2)

        # 多容器（按日期分组的 .history-list）：共享 resolver，跨容器消费顺序正确
        page.evaluate(
            """(function(){
                 ['g1','g2'].forEach(function(g){
                   const d=document.createElement('div'); d.className='rlist'; d.id=g;
                   d.innerHTML='<div class="rcard"><a href="https://www.bilibili.com/video/BVDUP">x</a></div>';
                   document.body.appendChild(d);
                 });
               })""")
        all_kids = page.evaluate(
            """(function(arg){
                 const cs=[document.getElementById('g1'), document.getElementById('g2')];
                 return BHF.Overlay.bootstrapAll({containers:cs, cardSel:'.rcard', records:arg.records, rule:arg.rule, now:arg.now, autoObserve:false})
                   .then(function(r){ return r.map(function(x){return x.kid;}); });
               })""",
            {"records": records2, "rule": rule, "now": NOW})
        check(all_kids == ["dupNew", "dupOld"], "bootstrapAll：多容器共享 resolver，跨容器按序消费", "got=%s" % all_kids)

    finally:
        browser.close(); p.stop(); srv.shutdown()

    print("\n" + "=" * 60)
    fails = [r for r in RESULTS if not r[0]]
    if not fails:
        print("  结果：全部通过（M3 overlay %d 项）" % len(RESULTS))
        return 0
    print("  结果：%d 通过 / %d 失败" % (len(RESULTS) - len(fails), len(fails)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
