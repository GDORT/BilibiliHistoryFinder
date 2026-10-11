# -*- coding: utf-8 -*-
"""B2 预演：在沙箱里验证 `owner=finder` 分支（Finder 自身抓取路径）。

⚠️ 本脚本**只在临时目录**里跑，**不碰真 data/**：
   - `copytree src/` 到 mkdtemp
   - 空 `data/`
   - `FETCHER_BASE` 指向死端口（让 analyzer.ok=False → 策略必然派 finder）
   - 随机空闲端口
   - 跑完 rmtree

目的是先看清 `owner=finder` 分支**会做什么**（在 blocked / 真跑 collector 之间），
再决定是否在真环境实测。
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


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(base, path, t=40):
    try:
        r = urllib.request.urlopen(base + path, timeout=t)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def post(base, path, payload=None, t=90, headers=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(base + path, method="POST", data=data, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=t)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def main():
    root = tempfile.mkdtemp(prefix="bhf_b2_")
    shutil.copytree(os.path.join(ROOT, "src"), os.path.join(root, "src"))
    os.makedirs(os.path.join(root, "data", "run"), exist_ok=True)
    port = free_port()
    # Analyzer 指向死端口 → analyzer.ok=False → 策略只能派 finder
    dead = "http://127.0.0.1:%d" % free_port()
    io.open(os.path.join(root, "config.json"), "w", encoding="utf-8").write(
        json.dumps({"web_port": port, "db_path": "data/bilibili_history.db",
                    "SESSDATA": "", "page_size": 20, "request_interval": 1.2}, indent=2))
    io.open(os.path.join(root, "data", "fetcher_config.json"), "w", encoding="utf-8").write(
        json.dumps({"base": dead, "api_key": ""}, indent=2))
    io.open(os.path.join(root, "data", "source_config.json"), "w", encoding="utf-8").write(
        json.dumps({"mode": "auto", "analyzer_db": os.path.join(root, "data", "ghost.db")}, indent=2))

    env = dict(os.environ)
    env["BHF_FETCHER_BASE"] = dead
    log = open(os.path.join(root, "server.log"), "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([sys.executable, os.path.join(root, "src", "server.py")],
                         stdout=log, stderr=subprocess.STDOUT, cwd=root, env=env)
    base = "http://127.0.0.1:%d" % port
    for _ in range(80):
        st, _b = get(base, "/", t=1)
        if st == 200:
            break
        time.sleep(0.25)
    try:
        st, body = get(base, "/api/capabilities", t=40)
        cap = json.loads(body)
        print("=== 沙箱：独立形态（Analyzer 指向死端口 %s）===" % dead)
        print("  mode            =", cap.get("mode"))
        print("  analyzer.ok     =", cap["connection"]["analyzer"].get("ok"))
        print("  finder.sessdata =", cap["connection"].get("finder", {}).get("sessdata"))
        print("  fetch 能力      =", json.dumps(cap["capabilities"]["fetch"], ensure_ascii=False))
        print("  plan            =", json.dumps(cap.get("plan") or {}, ensure_ascii=False))
        print()

        print("=== POST /api/sync（观察 owner 分派）===")
        st, b = post(base, "/api/sync", {})
        print("  HTTP", st)
        try:
            d = json.loads(b)
            for k in ("ok", "started", "blocked", "skipped", "engine", "mode", "reason", "error"):
                if k in d:
                    print("  %-9s = %s" % (k, str(d[k])[:160]))
            if "plan" in d:
                print("  plan      =", json.dumps(d["plan"], ensure_ascii=False)[:220])
        except Exception:
            print("  body:", b[:300])
        print()

        print("=== 结论判据 ===")
        try:
            d = json.loads(b)
            if d.get("blocked"):
                print("  ⇒ blocked：%s" % d.get("reason"))
                print("    **无 SESSDATA 时策略正确阻断**，不会硬跑 collector（不花风控额度）")
            elif d.get("engine") == "finder":
                print("  ⇒ engine=finder：策略派给本地 collector（= 你点「同步数据」的独立形态路径）")
            else:
                print("  ⇒ 其它分支，需人工判读")
        except Exception:
            pass
        print()

        print("=== 服务端日志尾部（看 collector 是否被拉起）===")
        log.flush()
        txt = io.open(os.path.join(root, "server.log"), encoding="utf-8", errors="replace").read()
        for ln in txt.splitlines()[-18:]:
            print("  " + ln[:150])
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
