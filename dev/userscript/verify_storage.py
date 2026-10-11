# -*- coding: utf-8 -*-
"""M0 验证：IndexedDB 双存储（raw 原样存 ＋ derived 规范化）。

用本地 HTTP 服务起一个真实 origin 的页面（IndexedDB 需要 origin），
把 core.js 注入后跑 Storage 的 round-trip。断言覆盖：
  · putBatch / count 正确
  · raw 层**原样**保留全部顶层键（不丢字段，红线）
  · derived 层从 history 子对象取 bvid/oid/cid，is_finish→progress=-1
  · 主键 kid 的幂等 upsert（重复 put 不增计数）
  · 输入 raw 对象不被 derive() 修改（引用安全）
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
PORT = 18931
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
        if self.path == "/" or self.path == "/index.html":
            body = b"<html><body><h1>storage test</h1></body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
        elif self.path.endswith("/core.js"):
            with open(SRC, "rb") as f: data = f.read()
            self.send_response(200); self.send_header("Content-Type", "application/javascript"); self.end_headers(); self.wfile.write(data)
        else:
            self.send_response(404); self.end_headers()


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
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
        # 真实结构样本：顶层 + history 子对象
        raws = [
            {"kid": "117314867432857", "title": "视频A", "long_title": "视频A长", "cover": "c1", "uri": "u1",
             "author_mid": "123", "author_name": "UP主甲", "author_face": "f1", "duration": 120, "progress": 30,
             "is_finish": False, "is_fav": True, "view_at": 1791536669, "videos": 3, "total": 3, "current": 1,
             "tag_name": "编程", "new_desc": "d1", "badge": "b1", "live_status": 0,
             "history": {"bvid": "BVAAAA", "oid": 555, "cid": 777, "epid": 0, "business": "archive", "page": 1, "part": "P1", "dt": "web"}},
            {"kid": "117314867432999", "title": "视频B", "duration": 0, "progress": 0, "is_finish": True,
             "view_at": 1791500000, "history": {"bvid": "BVBBBB", "oid": 666, "business": "live"}},
        ]
        out = page.evaluate(
            """(function(raws){
                 const S = window.BHF.Storage;
                 return S.clear().then(function(){
                   return S.putBatch(raws).then(function(){
                     return S.count().then(function(cnt){
                       return S.getAllRaw().then(function(rawAll){
                         return S.getAllDerived().then(function(derAll){
                           return S.getByKid(raws[0].kid).then(function(one){
                             return S.putItem(raws[0]).then(function(){   // 幂等 upsert
                               return S.count().then(function(cnt2){
                                 return {cnt:cnt, cnt2:cnt2, rawAll:rawAll, derAll:derAll, one:one,
                                         rawKeys: Object.keys(rawAll[0]).length,
                                         rawHasBadge: ('badge' in rawAll[0]),
                                         derBvid: derAll[0].bvid, derOid: derAll[0].oid,
                                         derBusiness: derAll[0].business,
                                         derProgress0: derAll[0].progress,
                                         derB1Finish: derAll[1].progress,   // is_finish=true → -1
                                         derB1Business: derAll[1].business,
                                         oneBvid: one.bvid};
                               });
                             });
                           });
                         });
                       });
                     });
                   });
                 });
               })""",
            raws)

        check(out["cnt"] == 2, "putBatch 后 count=2", "got=%s" % out["cnt"])
        check(out["cnt2"] == 2, "重复 put 同 kid 不增计数（upsert 幂等）", "got=%s" % out["cnt2"])
        check(len(out["rawAll"]) == 2, "getAllRaw 返回 2 条")
        check(out["rawKeys"] >= 20, "raw 顶层保留全部键（不丢字段）", "topKeys=%s" % out["rawKeys"])
        check(out["rawHasBadge"] is True, "raw 保留派生层不用的字段（badge）")
        check(out["derBvid"] == "BVAAAA", "derived 从 history 取 bvid")
        check(out["derOid"] == 555, "derived 从 history 取 oid")
        check(out["derBusiness"] == "archive", "derived 从 history 取 business")
        check(out["derProgress0"] == 30, "derived 普通 progress 不变")
        check(out["derB1Finish"] == -1, "is_finish=true → derived.progress=-1（归一化）")
        check(out["derB1Business"] == "live", "derived 第二条 business=live")
        check(out["oneBvid"] == "BVAAAA", "getByKid 返回正确 derived")

        # 引用安全：derive 不修改传入 raw
        ref_ok = page.evaluate(
            """(function(){
                 const raw = {kid:'x', title:'t', duration:10, progress:5, history:{bvid:'BVX', business:'archive'}};
                 const before = JSON.stringify(raw);
                 const d = window.BHF.Storage.derive(raw);
                 return {unchanged: before === JSON.stringify(raw), dur: d.duration, bvid: d.bvid};
               })""")
        check(ref_ok["unchanged"], "derive() 不修改传入 raw（引用安全）")
        check(ref_ok["dur"] == 10 and ref_ok["bvid"] == "BVX", "derive 输出正确")

    finally:
        browser.close(); p.stop(); srv.shutdown()

    print("\n" + "=" * 60)
    fails = [r for r in RESULTS if not r[0]]
    if not fails:
        print("  结果：全部通过（M0 存储 %d 项）" % len(RESULTS))
        return 0
    print("  结果：%d 通过 / %d 失败" % (len(RESULTS) - len(fails), len(fails)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
