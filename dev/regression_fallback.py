# -*- coding: utf-8 -*-
"""② 自动回退（增量 → 全量）的进程内回归。

被测分支：`src/server.py` 的 `GET /api/fetcher-trigger` 增量分支 ——
Analyzer 返回顶层 {"status":"error","message":"未找到本地历史记录…"} 时，
Finder 自动改发全量 `/fetch/bili-history`（对齐开源 Frontend 的回退策略）。

为什么「零副作用」：
  1. **不改磁盘配置**。`_fetcher_cfg()` 的优先级是
     `FETCHER_OVERRIDE`(内存 dict) > 环境变量 > `data/fetcher_config.json`
     → 只在内存里把 base 指向假 Analyzer，`data/fetcher_config.json` 一个字节不动。
     （这是 doc/待办-当前阶段.md §6.2 手工测法的升级：省掉「改配置 → 必须还原」整步。）
  2. **假 Analyzer 的全量支默认返回 503** → `full.ok=false` → 回退路径的 `post` 为 None。
  3. `_after_data_pull`（唯一会真写备份 + reload 的函数）被替换成**记录桩**；
     被测的分支判定逻辑本身完全真实。事后仍做零污染核对（config md5 / run / backup）。

拓扑（吸取上一轮教训）：**真起 `server.Handler` + 真发 HTTP 请求**，
不复制 `if ... fallback ...` 那段判定 —— 否则测的是副本，不是生产路径。

三条用例互为对照，证明判据既「够用」也「不过宽」：
  用例1 缺基线            → 必须回退（realtime → bili-history 两个请求）
  用例2 基线正常(ok)      → 不得回退（只有 realtime 一个请求）
  用例3 其它错误(非该消息) → 不得回退（证明不是「任何 error 都回退」）

不启动对外服务（只绑 127.0.0.1 临时端口）、不读写历史数据、不连 B站。
"""

import hashlib
import http.server
import importlib.util
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

_SAVED_PROXY = {}


def _kill_proxy():
    """本机有 HTTP 代理，会把 127.0.0.1 的请求也吃掉（curl 实测拿到 502）。
    urlopen 读同一批环境变量 → 进程内临时清空，跑完恢复。"""
    for k in ("http_proxy", "https_proxy", "all_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        if k in os.environ:
            _SAVED_PROXY[k] = os.environ.pop(k)
    for k in ("no_proxy", "NO_PROXY"):
        os.environ[k] = "127.0.0.1,localhost"


def _restore_proxy():
    for k, v in _SAVED_PROXY.items():
        os.environ[k] = v


def _load_mock():
    spec = importlib.util.spec_from_file_location(
        "mock_analyzer", os.path.join(HERE, "mock_analyzer.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mk_srv(handler_cls):
    class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    return Srv(("127.0.0.1", 0), handler_cls)


def _get(port, path, timeout=30):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, None


def _kind(path):
    """把请求路径归一成「增量 / 全量」——注意 startswith 会撞车，必须精确判。"""
    p = path.split("?")[0]
    if p == "/fetch/bili-history-realtime":
        return "增量"
    if p == "/fetch/bili-history":
        return "全量"
    return p


def _make_handler(mock_mod, inc_body, tag):
    """假 Analyzer：增量支按 inc_body 返回，其余路由沿用 mock_analyzer 的实现。"""
    hits = []

    class H(mock_mod.MockHandler):
        def _note(self, t, detail=""):
            hits.append(_kind(self.path))
            super()._note(t, detail)

        def _route(self):
            if self.path.split("?")[0] == "/fetch/bili-history-realtime":
                self._note(tag)
                return self._reply(200, inc_body)
            return super()._route()

    return H, hits


def _redirect_run_dir(server):
    """所有写盘改到临时目录 —— 绝不碰仓库真实的 data/run/（含护栏记账）。"""
    server.RUN_DIR = tempfile.mkdtemp(prefix="bhf_fb_")
    server.RESTARTS_FILE = os.path.join(server.RUN_DIR, "restarts.json")
    server.RESTART_LOG = os.path.join(server.RUN_DIR, "restart.log")
    server.RELAUNCH_FLAG = os.path.join(server.RUN_DIR, "relaunch.flag")


def _md5(p):
    try:
        with open(p, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def _ls(p):
    try:
        return sorted(os.listdir(p))
    except Exception:
        return None


def _snapshot():
    cfg = os.path.join(ROOT, "data", "fetcher_config.json")
    return {
        "fetcher_config.md5": _md5(cfg),
        "fetcher_config.mtime_ns": os.stat(cfg).st_mtime_ns if os.path.exists(cfg) else None,
        "run": _ls(os.path.join(ROOT, "data", "run")),
        "backup": _ls(os.path.join(ROOT, "data", "backup")),
    }


def main():
    _kill_proxy()
    mock_mod = _load_mock()

    import server
    _redirect_run_dir(server)

    # 唯一副作用函数 → 记录桩（被测的分支判定逻辑保持真实）
    pulled = []
    server._after_data_pull = lambda res: (pulled.append(res), {"stub": True})[1]

    before = _snapshot()
    print("=" * 74)
    print("  ② 自动回退 端到端回归 —— 真起 server.Handler，真发 HTTP GET")
    print("  base 走内存覆盖 FETCHER_OVERRIDE（data/fetcher_config.json 不动）")
    print("=" * 74)

    cases = [
        ("用例1 缺基线", mock_mod.NO_BASELINE, True,
         "Analyzer 报「未找到本地历史记录」", "必须回退 → 2 个请求(增量→全量)"),
        ("用例2 基线正常", {"status": "ok", "message": "增量完成"}, False,
         "增量返回正常 ok", "不得回退 → 1 个请求(仅增量)"),
        ("用例3 其它错误", {"status": "error", "message": "网络超时，请重试"}, False,
         "错误消息不含判据关键词", "不得回退 → 1 个请求(仅增量)"),
    ]

    passed = 0
    for name, inc_body, expect_fb, desc, expect_req in cases:
        pulled.clear()
        H, hits = _make_handler(mock_mod, inc_body, "增量 → %s" % json.dumps(inc_body, ensure_ascii=False)[:34])
        mock_srv = _mk_srv(H)
        mport = mock_srv.server_address[1]
        threading.Thread(target=mock_srv.serve_forever, daemon=True).start()

        server.FETCHER_OVERRIDE["base"] = "http://127.0.0.1:%d" % mport
        httpd = _mk_srv(server.Handler)
        aport = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        st, resp = _get(aport, "/api/fetcher-trigger?sync_deleted=1")

        got_fb = (resp or {}).get("fallback_to_full") is True
        seq = list(hits)
        if expect_fb:
            req_ok = seq == ["增量", "全量"]
        else:
            req_ok = seq == ["增量"]
        ok = (st == 200) and (got_fb == expect_fb) and req_ok
        passed += 1 if ok else 0

        print("\n[%s] %s" % ("PASS" if ok else "FAIL", name))
        print("      场景   : %s" % desc)
        print("      期望   : %s" % expect_req)
        print("      HTTP   : %d" % st)
        print("      回退   : fallback_to_full = %s（期望 %s）" % (got_fb, expect_fb))
        print("      假后端收到: %s" % (seq if seq else "（无）"))
        if got_fb:
            print("      message: %s" % (resp or {}).get("message"))
            print("      post   : %s（应为 None —— 全量 503 不触发 post）" % (resp or {}).get("post"))
        else:
            print("      post   : %s（记录桩；_after_data_pull 未被真执行）"
                  % ("已调用" if pulled else "未调用"))

        httpd.shutdown()
        httpd.server_close()
        mock_srv.shutdown()
        mock_srv.server_close()

    after = _snapshot()
    print("\n" + "=" * 74)
    print("  零污染核对")
    print("=" * 74)
    same = True
    for k in before:
        eq = before[k] == after[k]
        same = same and eq
        print("  [%s] %-24s %s" % ("OK " if eq else "!! ", k,
                                   "未变" if eq else "%s -> %s" % (before[k], after[k])))
    shutil.rmtree(server.RUN_DIR, ignore_errors=True)
    _restore_proxy()

    print("\n总体：%s（%d/%d 通过%s）" % (
        "全部通过" if (passed == len(cases) and same) else "存在失败",
        passed, len(cases),
        "，零污染" if same else "，⚠️ 有污染"))
    return 0 if (passed == len(cases) and same) else 1


if __name__ == "__main__":
    sys.exit(main())
