# -*- coding: utf-8 -*-
"""B2 真跑：验证 `owner=finder` 分支（Finder 自己抓取）**真发 B站请求**。

安全设计（ minimize 风控额度与污染）：
  - `copytree src/` 到 mkdtemp，**空 data/** → **绝不碰真库**
  - `FETCHER_BASE` / `fetcher_config.base` 指向死端口 → analyzer.ok=False → 策略必然派 finder
  - **`page_size=3`** → 只拉 3 条（探路够用，不做全量）
  - `request_interval` 拉大 → 降低风控概率
  - 随机端口，结束即 rmtree

⚠️ 会**真发 B站请求**（带 SESSDATA）。这是 B2 的核心目的。
"""
import io
import os
import sys
import json
import time
import shutil
import socket
import tempfile
import subprocess
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_SIZE = 3


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(base, path, t=60):
    try:
        r = urllib.request.urlopen(base + path, timeout=t)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def post(base, path, payload=None, t=180):
    req = urllib.request.Request(base + path, method="POST",
                                 data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=t)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def main():
    sess = (json.loads(io.open(os.path.join(ROOT, "config.json"), encoding="utf-8").read())
            .get("SESSDATA") or "")
    if len(sess) < 50:
        print("!! Finder config.json 的 SESSDATA 为空，无法测 B2。")
        return
    print("=== B2 真跑：Finder 自身抓取路径（真发 B站，page_size=%d）===" % PAGE_SIZE)
    print("  SESSDATA 长度 = %d（来自你的 config.json）" % len(sess))
    print()

    root = tempfile.mkdtemp(prefix="bhf_b2real_")
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(root, "src"))
    os.makedirs(os.path.join(root, "data", "run"), exist_ok=True)
    port = free_port()
    dead = "http://127.0.0.1:%d" % free_port()
    io.open(os.path.join(root, "config.json"), "w", encoding="utf-8").write(json.dumps({
        "web_port": port, "db_path": "data/bilibili_history.db",
        "SESSDATA": sess, "page_size": PAGE_SIZE, "request_interval": 2.5,
    }, indent=2))
    io.open(os.path.join(root, "data", "fetcher_config.json"), "w", encoding="utf-8").write(
        json.dumps({"base": dead, "api_key": ""}, indent=2))
    io.open(os.path.join(root, "data", "source_config.json"), "w", encoding="utf-8").write(
        json.dumps({"mode": "auto", "analyzer_db": os.path.join(root, "data", "ghost.db")}, indent=2))

    log = open(os.path.join(root, "server.log"), "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([sys.executable, os.path.join(root, "src", "server.py")],
                         stdout=log, stderr=subprocess.STDOUT, cwd=root)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        st, _b = get(base, "/", t=1)
        if st == 200:
            break
        time.sleep(0.25)
    try:
        st, body = get(base, "/api/capabilities", t=60)
        cap = json.loads(body)
        print("=== 沙箱形态确认 ===")
        print("  mode        =", cap.get("mode"))
        print("  analyzer.ok =", cap["connection"]["analyzer"].get("ok"))
        print("  sessdata    =", cap["connection"].get("finder", {}).get("sessdata"))
        print("  fetch       =", json.dumps(cap["capabilities"]["fetch"], ensure_ascii=False))
        print("  plan        =", json.dumps(cap.get("plan") or {}, ensure_ascii=False))
        print()

        print("=== POST /api/sync（owner 应为 finder → 真跑 collector）===")
        t0 = time.time()
        st, b = post(base, "/api/sync", {})
        print("  HTTP %s（%.1fs）" % (st, time.time() - t0))
        try:
            d = json.loads(b)
            for k in ("ok", "started", "blocked", "engine", "mode", "reason", "error"):
                if k in d:
                    print("  %-9s = %s" % (k, str(d[k])[:200]))
        except Exception:
            print("  body:", b[:400])
        print()

        print("=== 轮询进度（最多 150s）===")
        for i in range(30):
            time.sleep(5)
            st, b = get(base, "/api/sync", t=30)
            try:
                s = json.loads(b)
            except Exception:
                continue
            pr = s.get("progress") or {}
            print("  [%3ds] running=%-5s phase=%-8s fetched=%-4s total=%-6s err=%s"
                  % ((i + 1) * 5, s.get("running"), pr.get("phase"), pr.get("fetched"),
                     pr.get("total_estimate"), str(pr.get("error") or "")[:60]))
            if not s.get("running") and pr.get("phase") in ("done", "error"):
                print()
                print("=== 最终状态 ===")
                print("  progress =", json.dumps(pr, ensure_ascii=False)[:400])
                la = s.get("last") or {}
                print("  last     =", json.dumps(la, ensure_ascii=False)[:300])
                break
        print()

        print("=== 沙箱本地库落了多少条（真库不受影响）===")
        import sqlite3
        db = os.path.join(root, "data", "bilibili_history.db")
        if os.path.exists(db):
            c = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
            n = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]
            dur = c.execute("SELECT COUNT(*) FROM history WHERE duration IS NOT NULL AND duration>0").fetchone()[0]
            print("  history 行数 = %d ｜ 有 duration 的 = %d" % (n, dur))
            for row in c.execute("SELECT kid, duration, progress, business FROM history LIMIT 5"):
                print("    %-22s dur=%-6s prog=%-5s biz=%s" % row)
            c.close()
        else:
            print("  （未建库 → collector 未写入）")
        print()

        print("=== 服务端日志尾部 ===")
        log.flush()
        txt = io.open(os.path.join(root, "server.log"), encoding="utf-8", errors="replace").read()
        for ln in txt.splitlines()[-22:]:
            print("  " + ln[:160])
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        shutil.rmtree(root, ignore_errors=True)
        print()
        print("沙箱已清理（真 data/ 未被触碰）")


if __name__ == "__main__":
    main()
