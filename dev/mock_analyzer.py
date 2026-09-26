"""本地 mock Analyzer —— 用于离线复现 Finder 的「② 自动回退」分支。

背景（#3 / #20 / #32 ｜ 详见 doc/待办-当前阶段.md §6.2）：
    Finder 的 `GET /api/fetcher-trigger`（增量）在拿到 Analyzer 的响应后，会检查
    顶层 `status == "error"` 且 message 含「未找到本地历史记录」→ 说明 Analyzer 缺
    增量基线 → 自动改发全量 `/fetch/bili-history`（对齐开源 Frontend 的回退策略）。

    生产环境里 Analyzer 已有基线，这个分支**永远不会走到**，所以要用一个假后端把它逼出来。

安全性（这是本脚本存在的意义）：
    - 只绑 `127.0.0.1`，不对外；不连 B站；不读写任何真实历史库。
    - 全量那一支**默认返回 503** → Finder 侧 `ok=false` → `post`（自动备份判定 + reload）
      不会被触发 → 测试**零副作用**。
    - 想顺带验证 `post` 链路时，把全量改成 200：`set BHF_MOCK_FULL=200`（代价：可能真写一份备份快照）。

用法：
    python dev/mock_analyzer.py                 # 默认 http://127.0.0.1:8790
    set BHF_MOCK_PORT=8791 && python dev/mock_analyzer.py
    set BHF_MOCK_FULL=200  && python dev/mock_analyzer.py

    另开一个窗口（用完 Ctrl+C 关掉 mock）：
    curl --noproxy "*" -X POST -H "Content-Type: application/json" \
         -d "{\"base\":\"http://127.0.0.1:8790\"}" http://127.0.0.1:8765/api/fetcher-config
    curl --noproxy "*" http://127.0.0.1:8765/api/fetcher-trigger

    还原（必做）：
    curl --noproxy "*" -X POST -H "Content-Type: application/json" \
         -d "{\"base\":\"http://localhost:8899\"}" http://127.0.0.1:8765/api/fetcher-config
"""

import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("BHF_MOCK_PORT") or 8790)
FULL_STATUS = int(os.environ.get("BHF_MOCK_FULL") or 503)

# 判据原文：server.py 的增量分支要求**顶层**就有 status / message
NO_BASELINE = {"status": "error",
               "message": "未找到本地历史记录，请先执行一次全量同步"}


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---- 工具 ----
    def _reply(self, code, body):
        b = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _note(self, tag, detail=""):
        ts = datetime.now().strftime("%H:%M:%S")
        line = "[mock %s] %-6s %-42s %s" % (ts, self.command, self.path, tag)
        if detail:
            line += "  <- " + detail
        print(line, flush=True)

    # ---- 路由 ----
    def _route(self):
        p = self.path
        if p.startswith("/fetch/bili-history-realtime"):
            self._note("命中增量 → 故意报『未找到本地历史记录』", "触发回退")
            return self._reply(200, NO_BASELINE)
        if p.startswith("/fetch/bili-history"):
            if FULL_STATUS == 200:
                self._note("命中全量(=200) → 放行", "会连带触发 post")
                return self._reply(200, {"status": "ok", "message": "mock 全量完成（无真实拉取）"})
            self._note("命中全量 → 返回 %d（不触发 post）" % FULL_STATUS)
            return self._reply(FULL_STATUS, {"status": "error", "message": "mock：不真实拉取"})
        if p.startswith("/login/check"):
            self._note("凭证探测 → 回 mock 已登录")
            return self._reply(200, {"code": 0, "data": {"isLogin": True, "uname": "mock", "vipStatus": 0}})
        if p.startswith("/health"):
            return self._reply(200, {"status": "running", "mock": True,
                                     "timestamp": datetime.now().isoformat()})
        self._note("未定义路径 → 空壳 200")
        return self._reply(200, {"ok": True, "data": {}})

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    # 默认日志太吵且带时间戳格式不同，统一走 _note
    def log_message(self, *args):
        pass


def main():
    if os.environ.get("BHF_MOCK_PORT"):
        print("端口来自环境变量 BHF_MOCK_PORT=%d" % PORT)
    print("=" * 68)
    print("  mock Analyzer 运行中： http://127.0.0.1:%d" % PORT)
    print("  增量接口  -> 200 {\"status\":\"error\",\"message\":\"未找到本地历史记录…\"}")
    print("  全量接口  -> %d %s" % (FULL_STATUS,
                                    "(会触发 post：自动备份 + reload)" if FULL_STATUS == 200
                                    else "(不会触发 post → 测试零副作用)"))
    print("  按 Ctrl+C 停止。所有请求会打在上方日志里。")
    print("=" * 68)
    sys.stdout.flush()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), MockHandler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nmock 已停止。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
