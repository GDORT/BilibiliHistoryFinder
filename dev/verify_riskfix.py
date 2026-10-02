# -*- coding: utf-8 -*-
"""风险审查修复的自动化验收（第一批 5 条 + §8 收尾 6 条）。

设计原则（沿用 dev/ 四条安全约定）：
  · **真起生产代码**：把 `src/` **整目录复制**到临时目录，用 `python <tmp>/src/server.py`
    原样启动真实进程 —— 不 patch、不复制被测分支逻辑、不 mock `main()`。
  · **真发 HTTP**：`urllib` 直连（`ProxyHandler({})` 绕代理，否则本机 127.0.0.1 会走代理返 502）；
    需要"说谎的 Content-Length"时用裸 socket 手搓请求（urllib 会按 data 长度自动填，捏不了）。
  · **零污染**：临时副本自带空的 `data/`，`ANALYZER_DB` 指向**不存在的路径** ——
    全程不读真实 Analyzer 库、不碰真实 `data/`。脚本末尾核对真实仓库 5 个文件 md5 前后一致。
  · **只绑回环**：用的是脚本自动挑的空闲端口；跑完 `terminate` 子进程，不留后台服务。

覆盖：
  F-H1  监听地址      真进程只绑回环（netstat 逐条核对）+ 横幅/restart.log 如实记录 + 非回环连不上
  F-H3  请求体解析    `非 JSON` → 400 ／ `顶层非对象` → 400 ／ `空体` → 合法（不得 400）
  F-H2  数据源校验    坏路径 → 400 **且配置一字节未写**；好路径 → 200 且**才**写
  N-L3  原子写        配置写入走 tmp+replace（无 `.tmp` 残留、文件始终是合法 JSON）
  N-H1  失败可观测    `note_failure` 记录真实失败；`FileNotFoundError`（正常态）**不记**
  ---- §8 修复后遗留（2026-10-01 复核）----
  C-H1  请求体上限    Content-Length 超限 → **413**，且**不把 body 读进内存**
  C-M1  纯空白体       `"   "` → 400（旧行为是静默当空体 → 退化成空操作）
  C-H2  单一写盘语义   改 mode 后，配置文件里**其它键（如 `policy`）必须留存**
  C-M2  span/meta 留痕  库文件损坏 → 留痕；库不存在 → 不记（与 N-H1 同一口径）

用法：`python dev/verify_riskfix.py`
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")

RESULTS = []


def check(ok, label, extra=""):
    RESULTS.append((bool(ok), label, extra))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label, ("  ← " + extra) if extra else ""))
    return bool(ok)


def info(label, extra=""):
    print("       · %s%s" % (label, ("  " + extra) if extra else ""))


def md5(path):
    if not os.path.exists(path):
        return "<missing>"
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(method, url, body=None, timeout=8):
    """返回 (status, text)。**不走代理**（本机 127.0.0.1 走代理会 502）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def raw_get(port, path, timeout=10):
    """返回 (status, bytes) —— 取**二进制**响应体（导出类端点用，`http()` 会 decode 坏字节）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method="GET")
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def nonloopback_ipv4():
    """本机非回环 IPv4 候选（取不到就返回 []，对应断言自动降级为 SKIP）。"""
    out = []
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip and not ip.startswith("127."):
                out.append(ip)
    except Exception:
        pass
    return out


def raw_post(port, path, headers, body=b"", timeout=5):
    """裸 socket 手搓 POST → 返回整个响应文本。

    存在的理由：`urllib` 会按 `data` 长度**自动**填 `Content-Length`，
    捏不出"声称 5 MB、实际不发送"这种请求 —— 而 C-H1 要验的恰恰是
    **服务端在看到超限的长度时就返回，根本不去读**。
    """
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    req = ("POST %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n"
           % (path, port))
    for k, v in headers.items():
        req += "%s: %s\r\n" % (k, v)
    chunks = []
    try:
        s.sendall(req.encode("utf-8") + b"\r\n" + body)
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
    except OSError:
        pass          # 服务端可能在没读完 body 时就断连（C-H1 正是如此）
    finally:
        s.close()
    return b"".join(chunks).decode("utf-8", "replace")


def listening_addrs(port):
    """`netstat -ano` 里该端口的 LISTENING 本地地址（OS 级证据，独立于被测代码自称）。"""
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           errors="replace", timeout=20)
    except Exception:
        return None
    addrs = []
    for ln in (r.stdout or "").splitlines():
        parts = ln.split()
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        if "LISTEN" not in parts[3].upper():
            continue
        local = parts[1]
        if local.rsplit(":", 1)[-1] == str(port):
            addrs.append(local.rsplit(":", 1)[0].strip("[]"))
    return addrs


def build_sandbox():
    """临时副本：src/ 整目录 + 空 data/ + 指向不存在库的配置（绝不触真实数据）。"""
    root = tempfile.mkdtemp(prefix="bhf_riskfix_")
    shutil.copytree(SRC, os.path.join(root, "src"))
    os.makedirs(os.path.join(root, "data", "run"), exist_ok=True)
    port = free_port()
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"web_port": port, "db_path": "data/bilibili_history.db",
                   "sessdata": ""}, f, ensure_ascii=False, indent=2)
    # 后端指向必然连不上的端口 → 一切中继优雅降级，**不联网**
    with open(os.path.join(root, "data", "fetcher_config.json"), "w", encoding="utf-8") as f:
        json.dump({"base": "http://127.0.0.1:1", "api_key": ""}, f, indent=2)
    ghost = os.path.join(root, "data", "__no_such_analyzer__.db")
    with open(os.path.join(root, "data", "source_config.json"), "w", encoding="utf-8") as f:
        json.dump({"mode": "auto", "analyzer_db": ghost}, f, ensure_ascii=False, indent=2)
    return root, port, ghost


def spawn(root, ghost):
    """真实启动 `src/server.py`。`ANALYZER_DB` 指向沙箱内不存在的路径 ——
    即便配置解析失败回落默认，也**绝不会**读到真实 Analyzer 库。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["ANALYZER_DB"] = ghost      # 连"回落默认值"这条路也锁在沙箱里
    env.pop("BHF_PORT", None)
    buf = []

    p = subprocess.Popen([sys.executable, os.path.join(root, "src", "server.py")],
                         cwd=root, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", bufsize=1)

    def _reader():
        try:
            for ln in p.stdout:
                buf.append(ln.rstrip("\n"))
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()
    return p, buf


def stop(p):
    try:
        p.terminate()
        p.wait(timeout=10)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def wait_up(port, proc, buf, budget=20.0):
    t0 = time.time()
    while time.time() - t0 < budget:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main():
    sys.path.insert(0, SRC)
    import store  # 只为 N-H1 的纯函数断言；**不调用任何读源库的函数**

    print("=" * 68)
    print("风险审查「第一批」修复验收（真实进程 + 隔离副本 + 零污染）")
    print("=" * 68)

    # 真实仓库的基线指纹（跑完必须一致）
    watched = [os.path.join(ROOT, "data", n) for n in
               ("bilibili_history.db", "canonical_state.db", "source_config.json",
                "fetcher_config.json", "rules.json")]
    before = {p: md5(p) for p in watched}

    root, port, ghost = build_sandbox()
    print("沙箱：%s\n端口：%s（脚本自选空闲端口，只绑回环）" % (root, port))
    proc, buf = spawn(root, ghost)
    print("-" * 68)

    try:
        up = wait_up(port, proc, buf)
        if not up:
            print("  子进程输出：\n" + "\n".join("    " + x for x in buf[-25:]))
        check(up, "隔离实例已启动（真实 `python src/server.py`，未做任何 patch）")
        if not up:
            return 1

        base = "http://127.0.0.1:%d" % port
        log = os.path.join(root, "data", "run", "restart.log")
        banner = "\n".join(buf)

        # ---------- F-H1 监听地址 ----------
        print("\n-- F-H1 监听地址只绑回环")
        check(("已启动: http://127.0.0.1:%d" % port) in banner,
              "启动横幅如实显示 127.0.0.1", )
        check("0.0.0.0" not in banner and "http://[::]" not in banner,
              "横幅不再出现 0.0.0.0 / 通配地址")
        logtxt = open(log, encoding="utf-8").read() if os.path.exists(log) else ""
        check(("listen on 127.0.0.1:%d" % port) in logtxt,
              "restart.log 记录真实 bind_host = 127.0.0.1",
              [l for l in logtxt.splitlines() if "listen on" in l][-1:] and "")
        addrs = listening_addrs(port)
        if addrs is None:
            info("netstat 不可用 → 跳过 OS 级核对")
        else:
            info("netstat 该端口 LISTENING 本地地址", str(addrs))
            check(bool(addrs) and all(a in ("127.0.0.1", "::1") for a in addrs),
                  "OS 级证据：该端口只在回环监听（无 0.0.0.0 / 通配）")
        ips = nonloopback_ipv4()
        if not ips:
            info("未取到非回环 IPv4 → 跳过直连探测")
        else:
            reach = []
            for ip in ips[:3]:
                try:
                    with socket.create_connection((ip, port), timeout=1.5):
                        reach.append(ip)
                except OSError:
                    pass
            check(not reach, "非回环地址连不上（候选：%s）" % ",".join(ips[:3]),
                  ("竟然连上：" + ",".join(reach)) if reach else "")

        # ---------- F-H3 请求体解析 ----------
        print("\n-- F-H3 请求体解析：失败必须显式 400，不得静默当成空体")
        st, txt = http("POST", base + "/api/data-source", b'{"mode": "auto"   <<< broken')
        check(st == 400, "非 JSON 请求体 → 400", "HTTP %s" % st)
        check("合法 JSON" in txt or "json" in txt.lower(), "  错误信息可读", txt[:90])
        st, _ = http("POST", base + "/api/data-source", b"[1, 2, 3]")
        check(st == 400, "顶层非对象（数组）→ 400", "HTTP %s" % st)
        st, _ = http("POST", base + "/api/data-source", b"")
        check(st != 400, "空请求体 → 合法（不得 400）", "HTTP %s" % st)

        # ---------- F-H2 数据源校验 ----------
        print("\n-- F-H2 /api/data-source：先校验 → 再生效 → 最后落盘")
        cfg = os.path.join(root, "data", "source_config.json")
        m0 = md5(cfg)
        ghost2 = os.path.join(root, "data", "__another_ghost__.db")
        st, txt = http("POST", base + "/api/data-source", {"analyzer_db": ghost2})
        check(st == 400, "不存在的 Analyzer 库路径 → 400", "HTTP %s" % st)
        check("已拒绝" in txt or "不可用" in txt, "  明确告知已拒绝且未改动", txt[:110])
        check(md5(cfg) == m0, "**坏路径一字节都没写进 source_config.json**（旧实现会落盘）")
        with open(cfg, encoding="utf-8") as f:
            cur = json.load(f)
        check(cur.get("analyzer_db") == ghost, "  配置仍是旧值（未被污染）",
              "analyzer_db=%s" % cur.get("analyzer_db"))

        # 好路径（合法 Analyzer 结构的最小 sqlite）→ 才允许写
        good = os.path.join(root, "data", "__good__.db")
        import sqlite3
        con = sqlite3.connect(good)
        con.execute("CREATE TABLE bilibili_history_2026 (kid TEXT, bvid TEXT, title TEXT)")
        con.execute("INSERT INTO bilibili_history_2026 VALUES ('k1','BV1','t')")
        con.commit()
        con.close()
        st, _ = http("POST", base + "/api/data-source", {"analyzer_db": good})
        check(st == 200, "合法 Analyzer 库 → 200", "HTTP %s" % st)
        with open(cfg, encoding="utf-8") as f:
            cur = json.load(f)
        check(cur.get("analyzer_db") == good, "  只有校验通过才落盘", "analyzer_db=%s" % cur.get("analyzer_db"))

        # ---------- N-L3 原子写 ----------
        print("\n-- N-L3 配置写入原子性")
        check("_write_json_atomic" in open(os.path.join(SRC, "store.py"), encoding="utf-8").read(),
              "store 侧有原子写实现（save_policy 唯一写盘点已切换）")
        import inspect as _inspect
        check("_write_json_atomic" in _inspect.getsource(store.save_policy),
              "save_policy 走原子写（不再 open(...,'w') 直写）")
        check("note_failure" in _inspect.getsource(store.save_policy),
              "save_policy 读取失败留痕（FileNotFoundError 除外）")
        tmps = []
        for dp, _dn, fn in os.walk(os.path.join(root, "data")):
            tmps += [os.path.join(dp, x) for x in fn if x.endswith(".tmp")]
        check(not tmps, "跑完无 `.tmp` 残留", str(tmps))
        for n in ("source_config.json", "fetcher_config.json"):
            p = os.path.join(root, "data", n)
            ok = True
            try:
                json.load(open(p, encoding="utf-8"))
            except Exception:
                ok = False
            check(ok, "  %s 始终是合法 JSON（半截 JSON 会被下次启动静默当默认值）" % n)

        # ---------- C-H1 / C-M1 请求体的"长度"与"空白" ----------
        print("\n-- C-H1 / C-M1 请求体：上限 与 纯空白")
        cap = 1_000_000
        check("MAX_POST_BODY" in open(os.path.join(SRC, "server.py"), encoding="utf-8").read(),
              "server.py 定义了请求体上限常量 MAX_POST_BODY")
        # C-H1：**声称** 5 MB、实际一个字节都不发 —— 服务端必须在"读"之前就拒绝
        t0 = time.time()
        resp = raw_post(port, "/api/data-source",
                        {"Content-Type": "application/json",
                         "Content-Length": str(5 * cap)}, body=b"")
        dt = time.time() - t0
        check("413" in resp.split("\r\n", 1)[0], "Content-Length 超限 → 413",
              resp.split("\r\n", 1)[0])
        check("请求体过大" in resp, "  错误信息说明原因与上限")
        check(dt < 3.0, "  立刻返回（未读取 body → 不占内存）", "%.2fs" % dt)
        # 边界：刚好等于上限不发（不构造 1MB 数据，只验"上限之内不拦"）
        resp = raw_post(port, "/api/data-source",
                        {"Content-Type": "application/json",
                         "Content-Length": str(cap + 1)}, body=b"")
        check("413" in resp.split("\r\n", 1)[0], "  上限 +1 字节同样 413",
              resp.split("\r\n", 1)[0])
        resp = raw_post(port, "/api/data-source",
                        {"Content-Type": "application/json", "Content-Length": "-3"},
                        body=b"")
        check("400" in resp.split("\r\n", 1)[0], "负 Content-Length → 400",
              resp.split("\r\n", 1)[0])
        # C-M1：纯空白不再是"合法空体"
        st, txt = http("POST", base + "/api/data-source", b"   ")
        check(st == 400, "纯空白请求体 → 400（旧行为：静默当 {} 继续跑）", "HTTP %s" % st)
        check("json" in txt.lower() or "合法" in txt, "  错误信息可读", txt[:90])
        st, _ = http("POST", base + "/api/data-source", b"")
        check(st != 400, "零长度请求体仍合法（与 C-M1 的空白区分开）", "HTTP %s" % st)

        # ---------- C-H2 同一文件的单一写盘语义 ----------
        print("\n-- C-H2 source_config.json：两个写盘方不得互相清字段")
        # 模拟"阶段 5 已接上 save_policy"之后的文件形态：除 mode/analyzer_db 外还有 policy 段
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"mode": "auto", "analyzer_db": ghost, "policy": {"prefer": "local"},
                       "keep_me": "YES"}, f, ensure_ascii=False, indent=2)
        st, _ = http("POST", base + "/api/data-source", {"mode": "local"})
        check(st == 200, "改 mode → 200", "HTTP %s" % st)
        with open(cfg, encoding="utf-8") as f:
            cur = json.load(f)
        check(cur.get("mode") == "local", "  自己的键写对了", "mode=%s" % cur.get("mode"))
        check(cur.get("policy") == {"prefer": "local"},
              "**别人的键（policy）被保留**（旧实现从零重建 → 会清掉）",
              "policy=%s" % cur.get("policy"))
        check(cur.get("keep_me") == "YES", "  未知键同样保留（不越权删字段）",
              "keep_me=%s" % cur.get("keep_me"))
        check("_persisted" not in cur, "  `_persisted` 只回前端、**不写进文件**")
        # 顺手把配置恢复成沙箱基线，避免影响后续步骤
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"mode": "auto", "analyzer_db": ghost}, f, ensure_ascii=False, indent=2)

        # ---------- A3 / A5（阶段 2 修正）----------
        print("\n-- A5 /api/export/local/db：与 ⑤ 备份同源（sqlite3 在线快照，非裸读字节流）")
        st, _ = http("GET", base + "/api/export/local/db")
        check(st == 404, "  本地库不存在 → 404（不凭空建库）", "HTTP %s" % st)
        local_db = os.path.join(root, "data", "bilibili_history.db")
        con = sqlite3.connect(local_db)
        con.execute("CREATE TABLE history(kid TEXT PRIMARY KEY, bvid TEXT, view_at INTEGER)")
        con.execute("INSERT INTO history VALUES ('k9','BV9',9)")
        con.commit()
        con.close()
        st, blob = raw_get(port, "/api/export/local/db")
        check(st == 200 and blob[:16] == b"SQLite format 3\x00",
              "  返回的是**有效 sqlite 文件**（magic 头正确）",
              "HTTP %s 前 16 字节=%r" % (st, blob[:16]))
        leak = [f for f in os.listdir(tempfile.gettempdir()) if f.startswith("bhf_export_")]
        check(leak == [], "  临时快照文件已清理（不残留）", str(leak[:3]))

        print("\n-- A3 主源为 Analyzer 时 /api/local/delete 必须拒绝（否则「删了立刻被补回」）")
        fake_ana = os.path.join(root, "data", "__fake_analyzer__.db")
        con = sqlite3.connect(fake_ana)
        con.execute("CREATE TABLE bilibili_history_2026 "
                    "(kid TEXT, bvid TEXT, oid TEXT, view_at INTEGER, title TEXT)")
        con.execute("INSERT INTO bilibili_history_2026 "
                    "VALUES ('k1','BV1','1',1,'t')")
        con.commit()
        con.close()
        st, _ = http("POST", base + "/api/data-source",
                     {"mode": "auto", "analyzer_db": fake_ana})
        check(st == 200, "  切到**可读**主源（mode=auto）→ 200", "HTTP %s" % st)
        st, txt = http("POST", base + "/api/local/delete", {"kids": ["k1"]})
        d = json.loads(txt) if txt else {}
        check(st == 200 and d.get("blocked") is True and "立刻补回" in str(d.get("reason")),
              "  blocked + 说明原因（不再是「删了看不出效果」）",
              "HTTP %s %s" % (st, json.dumps(d, ensure_ascii=False)[:110]))
        st, _ = http("POST", base + "/api/data-source", {"mode": "local"})
        check(st == 200, "  切到 mode=local（独立形态：主源不参与合并）", "HTTP %s" % st)
        st, txt = http("POST", base + "/api/local/delete", {"kids": ["k1"]})
        d = json.loads(txt) if txt else {}
        check(st == 200 and d.get("blocked") is not True,
              "  独立形态下**放行**（这才是本端点的有效场景）",
              "HTTP %s %s" % (st, json.dumps(d, ensure_ascii=False)[:110]))

        # ---------- N-H1 失败可观测 ----------
        print("\n-- N-H1 失败可观测（收窄版：只覆盖源读取 + 配置写入）")
        store.clear_failures()
        store._warn_fs_error("t", FileNotFoundError("nope"), "p")
        check(store.recent_failures() == [], "FileNotFoundError（正常态）**不记**失败")
        store.note_failure("t.probe", ValueError("boom"), "path=X")
        fs = store.recent_failures()
        check(len(fs) == 1 and "ValueError" in fs[0]["error"] and fs[0]["where"] == "t.probe",
              "真实失败被记录（where + 类型 + 消息）", str(fs[:1]))
        check(store.note_failure(None, RuntimeError("x")) is None,
              "note_failure **永不抛异常**（返回 None）")
        src_store = open(os.path.join(SRC, "store.py"), encoding="utf-8").read()
        src_srv = open(os.path.join(SRC, "server.py"), encoding="utf-8").read()
        check("note_failure" in src_store and "note_failure" in src_srv,
              "两个模块都接入了失败可观测")
        for fn in ("_read_analyzer", "_read_local"):
            check("note_failure" in _inspect.getsource(getattr(store, fn)),
                  "  store.%s 失败留痕" % fn)
        check("_warn_fs_error" in src_store, "  文件系统类失败统一走 `_warn_fs_error`（排除不存在）")
        # C-M2：span / meta 五处**真正接上** `_warn_fs_error`（此前是零调用的死代码）
        n_call = src_store.count("_warn_fs_error(")
        check(n_call >= 5, "  `_warn_fs_error` 已接线（定义 + ≥5 处调用）",
              "出现 %d 次" % n_call)
        for fn in ("local_span_days", "analyzer_span_days", "local_meta"):
            check("_warn_fs_error" in _inspect.getsource(getattr(store, fn)),
                  "  store.%s 读失败留痕（它们是 R3 判据的输入）" % fn)
        # 行为验证：**损坏的库文件**必须留痕；**不存在的库**必须是正常态、不记
        store.clear_failures()
        store.local_span_days(db_path=os.path.join(root, "data", "__none__.db"))
        check(store.recent_failures() == [], "  库不存在 → 正常态，不记（与 N-H1 同口径）")
        garbage = os.path.join(root, "data", "__garbage__.db")
        with open(garbage, "w", encoding="utf-8") as f:
            f.write("this is definitely not a sqlite file")
        store.clear_failures()
        store.local_span_days(db_path=garbage)
        f1 = store.recent_failures()
        check(len(f1) == 1 and f1[0]["where"] == "store.local_span_days",
              "  库文件损坏 → 留痕（读失败不再伪装成'没有跨度'）", str(f1[:1]))
        store.clear_failures()
        store.local_meta(["span_peak_days"], db_path=garbage)
        f2 = store.recent_failures()
        check(len(f2) == 1 and f2[0]["where"] == "store.local_meta",
              "  `local_meta` 读失败 → 留痕（span_peak_days 就在这张表）", str(f2[:1]))
        check(store.local_span_days(db_path=garbage) ==
              {"count": 0, "oldest": None, "days": None},
              "  留痕**不改返回语义**（失败仍返回原来的兜底值）")
        # 端到端：把 source_config.json 弄坏 → **重启进程**（只有启动时才读一次配置）
        # 期望：服务照常起来（优雅降级，不崩）+ stderr 明确留痕（不再静默回落默认值）
        bad = os.path.join(root, "data", "source_config.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        stop(proc)
        time.sleep(0.4)
        proc, buf = spawn(root, ghost)
        up2 = wait_up(port, proc, buf)
        check(up2, "坏配置下仍能启动（优雅降级，不崩）")
        out = "\n".join(buf)
        check("[BHF][warn] server._load_source_config" in out,
              "  进程输出留痕：解析失败不再静默",
              [x for x in buf if "[BHF][warn]" in x][:1] and "")
        check("已启动: http://127.0.0.1:%d" % port in out,
              "  横幅照常输出（降级后仍可用）")

    finally:
        stop(proc)
        time.sleep(0.3)

    # ---------- 零污染 ----------
    print("\n" + "=" * 68)
    print("零污染核对（真实仓库 data/）")
    print("=" * 68)
    for p, h in before.items():
        now = md5(p)
        check(now == h, os.path.relpath(p, ROOT).replace("\\", "/"),
              "" if now == h else "md5 变化！%s → %s" % (h, now))
    check(proc.poll() is not None, "子进程已退出，未留后台服务")

    shutil.rmtree(root, ignore_errors=True)

    npass = sum(1 for ok, _, _ in RESULTS if ok)
    print("=" * 68)
    print("结果：%s（%d/%d 项通过）" % ("全部通过" if npass == len(RESULTS) else "存在失败项",
                                       npass, len(RESULTS)))
    print("=" * 68)
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
