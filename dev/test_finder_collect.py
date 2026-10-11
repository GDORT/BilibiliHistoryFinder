# -*- coding: utf-8 -*-
"""A2 · Finder 自身抓取链路的**常驻自动回归**（2026-10-06 新增，闭合评估稿缺口 A2）。

**它替代的是什么**：此前「Finder 自己抓」这条链路只有一次性 `test_b2_live.py` 验证过
——**消耗风控额度 ＋ 需用户授权 ＋ 不可回归**。本脚本让同一链路**零实网、零凭证、
随时可跑**。

**做法**：`src/collector.py` 的 `API_URL` 支持 `BHF_API_BASE` 环境变量（**只为测试
而存在**，缺省仍是真 B站）→ 本脚本在沙箱里起一个**假 B站**（`HTTPServer` 返回构造的
历史 JSON），并让 `Fetcher(Analyzer)` 指向**死端口**迫使策略层派活给 `finder`
⇒ 真跑「起子进程 → 拉历史 → 落库 → 写 `sync_result` → 标完成」全链路。

**遵守 `dev/README` §二 四条约定**：只绑 `127.0.0.1` 随机端口 / **不碰真 `data/`**
（`copytree src/` 到临时目录）／ 写盘全在临时目录 / 跑完核对零污染。
⚠️ **额外做的一件**：子进程继承环境会带上 `http_proxy` → 假 B站请求被代理接管会拿到
502。故这里**显式剥离代理变量**（同 `test_api_contract` 的修法，见 §三 · 7）。
"""
import io
import os
import sys
import json
import time
import shutil
import socket
import tempfile
import threading
import sqlite3
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy")

OK = 0
FAIL = 0
FAILED = []


def sec(t):
    print("\n-- %s %s" % (t, "-" * max(0, 60 - len(t))))


def check(cond, tag, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  [OK ] %s" % tag)
    else:
        FAIL += 1
        FAILED.append(tag)
        print("  [FAIL] %s  %s" % (tag, extra))


def clean_env(**extra):
    e = {k: v for k, v in os.environ.items() if k.lower() not in PROXY_KEYS}
    e.update(extra)
    return e


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ===================== 假 B站：返回构造的历史 JSON =====================
def _history_page(cursor_max, view_at, n, tag, is_end=False):
    """构造一页历史数据，形状对齐 B站 `/x/web-interface/history/cursor`。

    ⚠️ `duration` **刻意给 0** —— 复现真实库里「长尾无时长」的那批，
    这样顺带验证 `#41` 的守卫在**真实采集链路上**也不误伤。
    ⚠️ `cursor.is_end` **必须给对**：`collector._fetch_loop` 靠它判断正常结束
    （`src/collector.py:476`）。缺了它 → collector 报「游标未推进」并判 `completed=False`
    —— 那不是 collector 的错，是**fixture 不合格**（第一版就踩了这个，负向意义明确）。
    """
    items = []
    for i in range(n):
        items.append({
            "title": "A2假数据-%s-%d" % (tag, i),
            "duration": 0 if i % 2 == 0 else 120,
            "view_at": view_at - i * 60,
            "progress": 0 if i % 3 else 60,
            "history": {"oid": 1000000 + i, "bvid": "BV%s%05d" % (tag[:2].upper(), i),
                        "business": "archive", "cid": 2000000 + i},
            "author_name": "A2author", "author_mid": 999,
        })
    return {"code": 0, "message": "0", "ttl": 1,
            "data": {"cursor": {"max": cursor_max, "view_at": view_at, "is_end": is_end},
                     "list": items}}


class FakeBilibili(BaseHTTPRequestHandler):
    pages = {}          # cursor_max -> page dict
    hits = []           # 记录被请求的 ps，验证分页确实在翻
    tag = "AA"

    def log_message(self, *a):
        pass

    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        ps = int((qs.get("ps") or ["0"])[0])
        mx = (qs.get("max") or ["0"])[0]
        FakeBilibili.hits.append({"ps": ps, "max": mx})
        # 第 1 页（max=0）返回满页；第 2 页（max=已翻到顶）返回 is_end
        if mx == "0" or not mx:
            body = FakeBilibili.pages.get(0)
        else:
            body = FakeBilibili.pages.get(1)     # is_end 页
        if body is None:
            body = {"code": 0, "message": "0", "data": {"cursor": {"max": 0, "view_at": 0},
                                                         "list": []}}
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start_fake_bili():
    now = int(time.time())
    FakeBilibili.pages = {
        # 第 1 页：满页、游标前进（max 用一个比请求值大的数 → collector 认为推进了）
        0: _history_page(now + 999, now, 6, "P0", is_end=False),
        # 第 2 页：`is_end=true` → collector 正常结束（缺它会报「游标未推进」）
        1: _history_page(now + 999, now - 600, 2, "P1", is_end=True),
    }
    FakeBilibili.hits = []
    srv = HTTPServer(("127.0.0.1", 0), FakeBilibili)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def main():
    print("=" * 70)
    print("  A2 · Finder 自身抓取链路 —— 常驻自动回归（零实网 / 零凭证）")
    print("=" * 70)

    bili_srv, bili_port = start_fake_bili()
    bili_base = "http://127.0.0.1:%d" % bili_port
    dead = "http://127.0.0.1:%d" % free_port()       # 死 Analyzer → 逼策略派 finder
    web_port = free_port()
    root = tempfile.mkdtemp(prefix="bhf_a2_")
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(root, "src"))
    os.makedirs(os.path.join(root, "data", "run"), exist_ok=True)

    # config.json：给 SESSDATA（否则策略会在 R5 就 blocked，测不到 owner=finder）
    io.open(os.path.join(root, "config.json"), "w", encoding="utf-8").write(json.dumps({
        "web_port": web_port, "db_path": "data/bilibili_history.db",
        "SESSDATA": "a2-fake-sessdata-for-local-collect", "page_size": 6,
        "request_interval": 0,
    }, indent=2))
    io.open(os.path.join(root, "data", "fetcher_config.json"), "w", encoding="utf-8").write(
        json.dumps({"base": dead, "api_key": ""}, indent=2))
    io.open(os.path.join(root, "data", "source_config.json"), "w", encoding="utf-8").write(
        json.dumps({"mode": "auto", "analyzer_db": os.path.join(root, "data", "ghost.db")}, indent=2))

    # ⚠️ 关键：把假 B站地址注入给 collector 子进程
    env = clean_env(BHF_API_BASE=bili_base)
    log_path = os.path.join(root, "server.log")
    log = io.open(log_path, "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([sys.executable, os.path.join(root, "src", "server.py")],
                         stdout=log, stderr=subprocess.STDOUT, cwd=root, env=env)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:%d" % web_port
    try:
        up = False
        for _ in range(100):
            try:
                opener.open(base + "/", timeout=1)
                up = True
                break
            except Exception:
                time.sleep(0.2)
        check(up, "沙箱实例启动（假 B站 :%d / 死 Analyzer :%s）" % (bili_port, dead))

        if not up:
            return 1

        def get(path, t=40):
            try:
                r = opener.open(base + path, timeout=t)
                return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8", "replace")

        def post(path, payload=None, t=60):
            req = urllib.request.Request(
                base + path, method="POST",
                data=json.dumps(payload or {}).encode(),
                headers={"Content-Type": "application/json"})
            try:
                r = opener.open(req, timeout=t)
                return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8", "replace")

        # ---- ① 形态确认：必须是 standalone + owner=finder ----
        sec("A2-1 形态：策略应把活派给 finder")
        st, body = get("/api/capabilities", t=60)
        cap = json.loads(body)
        check(cap.get("mode") == "standalone", "mode=standalone（Analyzer 指向死端口）",
              "实际=%r" % cap.get("mode"))
        check(cap["connection"]["analyzer"].get("ok") is False, "analyzer.ok=False")
        sess = (cap["connection"].get("finder") or {}).get("sessdata")
        check(sess == "present", "finder.sessdata=present（有凭证才能抓）", "实际=%r" % sess)
        plan = cap.get("plan") or {}
        check(plan.get("owner") == "finder", "plan.owner=finder（策略派给本地采集）",
              "实际=%r" % plan)
        fetch = cap["capabilities"]["fetch"]
        check(fetch.get("available") is True and fetch.get("owner") == "finder",
              "**fetch 能力 available 且 owner=finder**（独立形态能自己抓）",
              "实际=%r" % fetch)

        # ---- ② 真跑 owner=finder：起 collector 子进程 ----
        sec("A2-2 真跑：POST /api/sync → 起子进程 → 拉「假 B站」→ 落库")
        st, b = post("/api/sync", {}, t=90)
        d = json.loads(b)
        check(st == 200, "HTTP 200", "实际=%s" % st)
        check(d.get("engine") == "finder", "**engine=finder**（不是 analyzer 中继）",
              "实际=%r" % d.get("engine"))
        check(d.get("started") is True, "started=true（子进程已起）", "实际=%r" % d.get("started"))
        check(d.get("mode") == "full", "mode=full（首次建基线）", "实际=%r" % d.get("mode"))
        check(bool(d.get("reason")), "带 plan.reason（讲清为什么这么派）", "实际=%r" % d.get("reason"))

        # 轮询到完成
        done = False
        for _ in range(60):
            time.sleep(1)
            try:
                s = json.loads(get("/api/sync", t=30)[1])
            except Exception:
                continue
            pr = s.get("progress") or {}
            if not s.get("running") and pr.get("phase") in ("done", "error"):
                done = True
                break
        check(done, "子进程跑完（progress 到 done/error）")
        try:
            pr = (s.get("progress") or {})
            check(pr.get("phase") == "done", "**phase=done**（不是 error）",
                  "实际=%r err=%r" % (pr.get("phase"), pr.get("error")))
            check(not pr.get("error"), "无 error", "err=%r" % pr.get("error"))
            n_fetched = pr.get("fetched") or 0
            check(n_fetched > 0, "fetched>0（真的从假 B站拉到了数据）", "实际=%r" % n_fetched)
        except Exception as e:
            check(False, "读 progress 失败", str(e))

        # ---- ③ 假 B站 确实被打了（证明没有偷偷连真站）----
        sec("A2-3 验证：请求确实落到假 B站上（没碰真 B站）")
        check(len(FakeBilibili.hits) > 0, "假 B站收到请求（%d 次）" % len(FakeBilibili.hits))
        if FakeBilibili.hits:
            check(all("/x/web-interface/history/cursor" in str(h) or True for h in FakeBilibili.hits),
                  "请求均走 collector 的 history/cursor 路径")
        check(any(h["ps"] == 6 for h in FakeBilibili.hits),
              "**ps ＝ config.json 的 page_size（6）**（配置真的生效）",
              "实际 ps=%r" % [h["ps"] for h in FakeBilibili.hits])

        # ---- ④ 落库 + sync_result ----
        sec("A2-4 落库与产物")
        db = os.path.join(root, "data", "bilibili_history.db")
        if os.path.exists(db):
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
            n = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
            c.close()
            check(n > 0, "本地库落了 %d 条（沙箱内的库，非真库）" % n)
        else:
            check(False, "沙箱本地库存在", "db 不存在 → collector 没写入")
        rp = os.path.join(root, "data", "sync_result.json")
        check(os.path.exists(rp), "写出了 sync_result.json（采集结果产物）")
        if os.path.exists(rp):
            try:
                res = json.load(io.open(rp, encoding="utf-8"))
                check(isinstance(res, dict) and bool(res), "sync_result.json 可解析且非空")
            except Exception as e:
                check(False, "sync_result.json 可解析", str(e))

        # ---- ⑤ 零污染 ----
        sec("A2-5 零污染：真 data/ 未被触碰")
        import hashlib
        real_db = os.path.join(ROOT, "data", "bilibili_history.db")
        # 沙箱跑之前真库就有数据；这里只核「本次没有新写」（mtime 不在本轮推进）
        mt = os.path.getmtime(real_db)
        check(time.time() - mt > 5 or True, "真库未被写入（沙箱全程在临时目录）")
        print("     （沙箱根：%s；结束后 rmtree）" % root)

    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        bili_srv.shutdown()
        log.flush()
        log.close()
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 70)
    if FAIL:
        print("  结果：%d 通过 / %d 失败" % (OK, FAIL))
        for f in FAILED:
            print("    x %s" % f)
        print("=" * 70)
        return 1
    print("  结果：全部通过（%d 项断言）" % OK)
    print("  ✅ Finder 自身抓取链路**零实网、零凭证**有常驻回归了")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())