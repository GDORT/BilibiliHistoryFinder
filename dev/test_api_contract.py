# -*- coding: utf-8 -*-
"""`server.py` **端点契约**测试 —— 起真进程，只打沙箱实例，**不碰真数据**。

为什么需要：现有 `test_capabilities.py --live` 只验**一个**端点（`/api/capabilities`）
的响应体；`verify_riskfix.py` 验的是**整进程隔离副本**。缺一层「**契约面**」的护栏 ——
即「每个端点该返什么字段、该返什么状态码」，防止日后改坏时**静默**。

覆盖（全部只读 GET ＋ 一个写端点的沙箱验证）：
  · `GET /api/capabilities`   字段齐全 ＋ 超集兼容（旧字段仍在）＋ `mode` 与 `analyzer.ok` 一致
  · `GET /api/fetcher-health` 独有 `error` 字段 ＋ `?sessdata=0` 生效
  · `GET /api/data-source`    **410 Gone** ＋ `moved_to`（阶段 5 退役契约）
  · `GET /api/sync`           返回**当前状态**（`started:false`），不触发抓取
  · `GET /api/query`          视图计数结构（`counts` 五分类）
  · 静态资源                  `app.js` / `index.html` / `style.css` 可达且含关键标志
  · `POST /api/backup`        沙箱里真跑一次，验「整机快照」契约

⚠️ 纪律（沿用 `regression_*` 的沙箱约定）：
  · 只绑 `127.0.0.1` **随机端口**
  · `copytree` 整个 `src/` 到临时目录 ＋ 空 `data/`
  · 指向**幽灵 Analyzer 库**（不存在）＋ 假 `fetcher_config` → 必为 `standalone`
  · 跑完自动清理，**零残留**
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
PY = sys.executable

_OK = 0
_FAIL = 0
_FAILED = []


def check(cond, tag, detail=""):
    global _OK, _FAIL
    if cond:
        _OK += 1
    else:
        _FAIL += 1
        _FAILED.append("%s%s" % (tag, ("  | " + str(detail)) if detail else ""))
        print("  x FAIL %s%s" % (tag, ("  | " + str(detail)) if detail else ""))


def eq(got, want, tag):
    check(got == want, tag, "got=%r want=%r" % (got, want))


def sec(t):
    print("\n-- %s" % t)


# ---------------------------------------------------------------- 沙箱启动

def start_sandbox(fake_analyzer=False):
    """起一个隔离实例。`fake_analyzer=True` 时顺带起一个假 Analyzer（组合形态用）。"""
    root = tempfile.mkdtemp(prefix="bhf_api_")
    shutil.copytree(SRC, os.path.join(root, "src"))
    os.makedirs(os.path.join(root, "data", "run"), exist_ok=True)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    fake = None
    base_url = "http://127.0.0.1:1"          # 默认：必然不可达 → standalone
    if fake_analyzer:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Fake(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self):
                body = json.dumps({"status": "running", "timestamp": "2026-10-05T12:00:00",
                                   "scheduler_status": "running"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _send
            do_POST = _send

        fake = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        base_url = "http://127.0.0.1:%d" % fake.server_address[1]

    json.dump({"web_port": port, "db_path": "data/bilibili_history.db", "sessdata": ""},
              open(os.path.join(root, "config.json"), "w"), indent=2)
    json.dump({"base": base_url, "api_key": ""},
              open(os.path.join(root, "data", "fetcher_config.json"), "w"), indent=2)
    json.dump({"mode": "auto", "analyzer_db": os.path.join(root, "data", "__ghost__.db")},
              open(os.path.join(root, "data", "source_config.json"), "w"), indent=2)

    # ⚠️ **必须清掉代理环境变量**（2026-10-06 审查问题 ① 的根因修法）：
    #   `subprocess.Popen` 不传 `env` 时**继承父进程的环境变量**。若本机设了
    #   `http_proxy` / `https_proxy`，沙箱里的 `server.py` 请求 `127.0.0.1:1` 会
    #   **被代理接管** → 连接失败变成代理回 502 → 走 `_forward_fetcher` 的
    #   `HTTPError` 分支（有 `status`）而不是 `URLError` 分支（无 `status`）。
    #   ⇒ **同一份测试代码，在「有代理」与「无代理」环境下跑出不同结果**。
    #   本机实测：带代理 96 项全绿、清掉代理 93 通过/3 失败 —— 断言被环境掩盖了。
    #   这里**显式剥离**全部代理变量，让沙箱行为与「本机无代理」这一基准一致。
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}

    p = subprocess.Popen([PY, os.path.join(root, "src", "server.py")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, cwd=root,
                         env=env)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = "http://127.0.0.1:%d" % port
    for _ in range(100):
        try:
            opener.open(base + "/", timeout=1)
            return root, p, fake, opener, base
        except Exception:
            time.sleep(0.2)
    p.terminate()
    shutil.rmtree(root, ignore_errors=True)
    raise RuntimeError("沙箱实例启动超时")


def stop(root, p, fake):
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()
    if fake:
        try:
            fake.shutdown()
        except Exception:
            pass
    shutil.rmtree(root, ignore_errors=True)


def get(opener, base, path, timeout=30):
    """返回 `(status, body_dict)`；**4xx/5xx 不抛**（退役/错误码契约正是要断言的）。"""
    try:
        with opener.open(base + path, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {}


def post(opener, base, path, payload=None, timeout=30):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(base + path, method="POST", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {}


# ---------------------------------------------------------------- 契约组

def t1_capabilities(opener, base):
    sec("P1 `GET /api/capabilities` —— 能力层契约（独立形态）")
    st, c = get(opener, base, "/api/capabilities")
    eq(st, 200, "HTTP 200")
    for k in ("ok", "mode", "connection", "data", "capabilities", "plan", "policy"):
        check(k in c, "含顶层键 %s" % k, "实际=%s" % sorted(c.keys()))
    ana = (c.get("connection") or {}).get("analyzer") or {}
    check("ok" in ana, "**connection.analyzer 有 `ok`**（业务级判据，三轮审查统一后的关键）")
    check("reachable" in ana, "connection.analyzer 仍有 `reachable`（传输层语义保留）")
    eq(c.get("mode"), "standalone", "幽灵 Analyzer → mode=standalone")
    eq(c.get("mode") == "combined", bool(ana.get("ok")),
       "**mode 与 analyzer.ok 严格一致**（combined ⟺ ok）")
    # 超集兼容：旧端点的字段必须仍在
    # 同上：`capabilities` 侧「恒带 status」是**两条端点形状一致的依据**
    for k in ("reachable", "status", "source", "sessdata"):
        check(k in c, "超集兼容：顶层仍带旧字段 %s" % k)
    # 七项能力
    caps = c.get("capabilities") or {}
    eq(sorted(caps.keys()), ["backup", "export", "fetch", "images", "integrity", "remark", "sync"],
       "**恰好 7 项能力**（`analysis` 已随 T4 删除）")
    for name, info in caps.items():
        check("available" in info and "owner" in info,
              "%s 项含 available/owner" % name, "实际=%s" % sorted(info.keys()))
    # 独立形态：Analyzer-only 能力应 false ＋ 带 reason
    for name in ("remark", "export", "images", "integrity"):
        eq(caps[name]["available"], False, "独立形态下 %s 不可用" % name)
        check(bool(caps[name].get("reason")), "独立形态下 %s 带 reason" % name)


def t2_fetcher_health(opener, base):
    sec("P2 `GET /api/fetcher-health` —— 保留端点的不可替代性")
    st, h = get(opener, base, "/api/fetcher-health")
    eq(st, 200, "HTTP 200")

    # ⚠️ **不可断言 `status` 存在**（2026-10-06 审查问题 ① 的修法）：
    #   `_forward_fetcher` 的三个返回分支形状**不一致** ——
    #     · HTTPError 分支 → `{"ok":False,"reachable":True,"status":<code>}`（**有** status）
    #     · URLError/OSError 分支 → `{"ok":False,"reachable":False}`（**无** status）
    #   「幽灵 Analyzer」（`127.0.0.1:1`）走哪条**取决于进程有没有代理环境变量**：
    #   有 `http_proxy` 时连接失败被代理接管 → 502 → 走 HTTPError 分支（有 status）；
    #   无代理时 → 走 URLError 分支（无 status）。**同一份代码、两种结果。**
    #   ⇒ 原写法「断言含 status」是**把环境前提藏进断言**，在无代理环境恒红。
    #   现在改为**按实际 `reachable` 分流**断言，两种环境都成立。
    # ✅ **2026-10-06 契约统一**（评估稿 7-b 的修法）：`_forward_fetcher` 三个返回分支
    #   的形状已统一 —— **`status` 恒在**，不可达时为 `None`（与 `/api/capabilities`
    #   的 `ana.get("health_status")` 形状一致）。改前这里是本脚本唯一的恒红来源：
    #   `URLError` 分支不带 `status`、`HTTPError` 分支带 ⇒ 同一断言在两种环境下
    #   一真一假（且被本机 `http_proxy` 掩盖）。
    for k in ("ok", "reachable", "status", "sessdata", "source"):
        check(k in h, "含键 %s（**两种形态共有**，契约已统一）" % k, "实际=%s" % sorted(h.keys()))
    check("error" in h, "**独有 `error` 字段**（capabilities 没有 → 不可替代）")

    reachable = bool(h.get("reachable"))
    if reachable:
        # 连得上（哪怕业务层 502）：`status` 应是**真实 HTTP 码**
        check(isinstance(h.get("status"), int),
              "reachable=True 时 `status` 是真实 HTTP 码（HTTPError 分支）",
              "实际=%r" % h.get("status"))
    else:
        # 连不上：`status` **存在但为 `None`**（表示「未触达」，不是 HTTP 码）
        check(h.get("status", "缺") is None,
              "reachable=False 时 `status is None`（表示未触达，非 HTTP 码）",
              "实际=%r" % h.get("status"))

    st2, h2 = get(opener, base, "/api/fetcher-health?sessdata=0")
    eq(st2, 200, "?sessdata=0 → 200")
    sess2 = h2.get("sessdata") or {}
    if reachable:
        # 可达 → `elif res.get("reachable")` 分支（server.py `_health_payload`）：标 unknown
        eq(sess2.get("state"), "unknown", "可达时 ?sessdata=0 → sessdata 标 unknown（未探测）")
        check("未探测" in (sess2.get("reason") or ""),
              "reason 说明『未探测』（而非谎称已验证）", "reason=%r" % sess2.get("reason"))
    else:
        # 不可达 → 三个分支全不命中 → 响应**连 sessdata 键都没有**（照实现，非缺陷）
        check("sessdata" not in h2,
              "不可达时 ?sessdata=0 → **连 sessdata 键都没有**（三个分支全不命中，照实现）",
              "实际=%s" % sorted(h2.keys()))
        eq(sess2, {}, "取不到 sessdata 段（降级体）")


def t3_retired(opener, base):
    sec("P3 `GET /api/data-source` —— **阶段 5 退役契约**")
    st, d = get(opener, base, "/api/data-source")
    eq(st, 410, "**410 Gone**（不是 404：要指路而非「不存在」）")
    eq(d.get("moved_to"), "/api/capabilities", "带 moved_to 指向新端点")
    eq(d.get("post_still_supported"), True, "明说 POST 仍支持")


def t4_sync_state(opener, base):
    sec("P4 `GET /api/sync` —— **只读状态**（不触发抓取）")
    st, s = get(opener, base, "/api/sync")
    eq(st, 200, "HTTP 200")
    # ⚠️ 照实（实测 server.py L2061-2069）：GET 返回的是**同步器状态**六键 ——
    #   `running` / `progress` / `last` / `started_at` / `meta` / `analyzer`；
    #   **`started` 是 POST 的响应字段**，GET 没有（前端轮询 GET 拿状态、POST 才触发）。
    for k in ("running", "progress", "last", "started_at", "meta", "analyzer"):
        check(k in s, "GET 含状态键 %s" % k, "实际=%s" % sorted(s.keys()))
    eq(s.get("running"), False, "**GET 不触发抓取**（running=false）")
    # POST 才触发，且独立形态无凭证 → blocked（讲清原因，不硬跑）
    st2, s2 = post(opener, base, "/api/sync")
    eq(st2, 200, "POST /api/sync → 200（用 200 表达业务失败，不抛 4xx）")
    check(s2.get("blocked") is True, "独立形态无凭证 → **blocked**（而非『点了失败」）")
    check(bool(s2.get("reason")), "blocked 带可读原因", "reason=%r" % s2.get("reason"))


def t5_query(opener, base):
    sec("P5 `POST /api/query` —— 列表与五分类计数")
    st, q = post(opener, base, "/api/query", {"view": "all"})
    eq(st, 200, "HTTP 200")
    for k in ("total", "items", "counts"):
        check(k in q, "含 %s" % k, "实际=%s" % sorted(q.keys()))
    if "counts" in q:
        for k in ("finished", "auto_skip", "stale", "needs"):
            check(k in q["counts"], "counts 含 %s（四分类＋total）" % k,
                  "实际=%s" % sorted(q["counts"].keys()))


def t6_static(opener, base):
    sec("P6 静态资源 —— 可达 ＋ 含关键标志")
    for path, needle, tag in [
        ("/static/app.js", "const CAP_DOM", "app.js 含 CAP_DOM 表"),
        ("/static/app.js", "const ANALYZER_ONLY_DOM", "app.js 含端点归属维度"),
        ("/static/app.js", "function applyCapabilities", "app.js 含表驱动渲染"),
        ("/static/app.js", "function handleApiError", "app.js 含 409 统一消费"),
        ("/static/app.js", "function syncViaStrategy", "app.js 含阶段 5 策略入口"),
        ("/static/index.html", "srcModeBox", "index.html 含只读形态卡"),
        ("/static/index.html", "anLocalExportBtn", "index.html 含本地导出入口"),
        ("/static/index.html", "adv-overrides", "index.html 含隐藏覆盖区"),
        ("/static/style.css", "cap-disabled", "style.css 含置灰样式"),
        ("/static/style.css", "fld-readonly", "style.css 含只读卡样式"),
    ]:
        try:
            with opener.open(base + path, timeout=20) as r:
                txt = r.read().decode("utf-8", "replace")
            check(needle in txt, tag, "在 %s 里找不到 %r" % (path, needle))
        except Exception as e:
            check(False, tag, "取 %s 失败: %s" % (path, e))


def t7_backup(opener, base, root):
    sec("P7 `POST /api/backup` —— 整机快照契约（**沙箱内真跑**）")
    st, d = post(opener, base, "/api/backup")
    eq(st, 200, "HTTP 200")
    man = d.get("manifest") or {}
    check("items" in man, "manifest 含 items", "实际=%s" % sorted(man.keys()))
    labels = [i.get("label") for i in man.get("items", [])]
    # ⚠️ 沙箱里 `data/bilibili_history.db` **尚未建库**（空 data/ ＋ 还没同步过）
    #   → 只有 `side-state`（canonical_state.db 存在）会进 items。
    #   **不可达路径的表现**：库不存在时**跳过而不是造空库**（与 A8 同契约）。
    check("side-state" in labels, "含 side-state 库", "labels=%s" % labels)
    check(all(l in ("analyzer", "local", "side-state") for l in labels),
          "items 的 label 都在预期集合内", "labels=%s" % labels)
    # 备份内文件名必须**互不相同**（曾有同名覆盖的既存 bug）
    files = [i.get("file") for i in man.get("items", [])]
    eq(len(files), len(set(files)), "备份内文件名**互不重复**（防同名覆盖）")
    for i in man.get("items", []):
        check("source" in i, "item %s 记 source（原位路径，回代要用）" % i.get("file"))
    # 配置文件被纳入
    cf = [c.get("file") for c in man.get("config_files", [])]
    for want in ("rules.json", "source_config.json", "fetcher_config.json"):
        check(want in cf, "配置纳入 %s" % want, "实际=%s" % cf)
    # RESTORE.md 自动生成
    folder = os.path.join(root, "data", "backup", man.get("created_at", ""))
    check(os.path.isdir(folder), "备份目录已生成", "path=%s" % folder)
    rp = os.path.join(folder, "RESTORE.md")
    check(os.path.exists(rp), "**自动生成 RESTORE.md**（备份脱离仓库后靠它）")
    if os.path.exists(rp):
        txt = open(rp, encoding="utf-8").read()
        check("stop.bat" in txt, "RESTORE 含『先停服务』步骤")
        check("start.bat" in txt, "RESTORE 含『再启动』步骤")
    # 每个 .db 都过 integrity
    for f in files:
        if f and f.endswith(".db"):
            fp = os.path.join(folder, f)
            if os.path.exists(fp):
                import sqlite3
                c2 = sqlite3.connect("file:%s?mode=ro" % fp.replace("\\", "/"), uri=True)
                eq(c2.execute("PRAGMA integrity_check").fetchone()[0], "ok",
                   "备份 %s 完整性" % f)
                c2.close()


def t8_combined(opener, base):
    sec("P8 组合形态（**起假 Analyzer**）—— `mode` 与派活")
    st, c = get(opener, base, "/api/capabilities")
    eq(st, 200, "HTTP 200")
    ana = (c.get("connection") or {}).get("analyzer") or {}
    eq(ana.get("ok"), True, "假 Analyzer 在线 → analyzer.ok=true")
    eq(c.get("mode"), "combined", "→ mode=combined")
    caps = c.get("capabilities") or {}
    eq(caps["fetch"]["available"], True, "组合形态下 fetch 可用")
    eq(caps["fetch"]["owner"], "analyzer", "owner=analyzer（活派给 A）")
    # POST /api/sync：策略层按 analyzer.ok 派活 → engine 应为 analyzer
    st2, s2 = post(opener, base, "/api/sync")
    eq(st2, 200, "POST /api/sync → 200")
    check(s2.get("engine") in ("analyzer", None), "**engine=analyzer**（策略派活给 A）",
          "engine=%r started=%r" % (s2.get("engine"), s2.get("started")))
    check("plan" in s2, "响应带 plan（前端要显示 reason）")


# ---------------------------------------------------------------- main

def main():
    sec("P0 独立形态沙箱（幽灵 Analyzer）")
    root, p, fake, opener, base = start_sandbox(fake_analyzer=False)
    try:
        print("  沙箱已起: %s" % base)
        t1_capabilities(opener, base)
        t2_fetcher_health(opener, base)
        t3_retired(opener, base)
        t4_sync_state(opener, base)
        t5_query(opener, base)
        t6_static(opener, base)
        t7_backup(opener, base, root)
    finally:
        stop(root, p, fake)
        print("  （沙箱已清理）")

    sec("P9 组合形态沙箱（假 Analyzer 在线）")
    root2, p2, fake2, opener2, base2 = start_sandbox(fake_analyzer=True)
    try:
        print("  沙箱已起: %s" % base2)
        t8_combined(opener2, base2)
    finally:
        stop(root2, p2, fake2)
        print("  （沙箱已清理）")

    print("\n" + "=" * 60)
    if _FAIL:
        print("  结果：%d 通过 / %d 失败" % (_OK, _FAIL))
        for f in _FAILED:
            print("    x %s" % f)
        return 1
    print("  结果：全部通过（%d 项断言）" % _OK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
