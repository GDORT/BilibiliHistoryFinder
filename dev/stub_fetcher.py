#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开发用 Analyzer/Fetcher 控制后端桩（仅用于无真实 Analyzer 时的前端验证）。

为什么需要它：
    网页「实时更新」按钮经 server.py 转发到 BilibiliHistoryFetcher 后端（默认
    http://localhost:8899）。要验证按钮→触发→刷新的完整闭环，本机必须有一个
    监听 8899 的「控制后端」。真实 Analyzer 未开启时，用本桩模拟即可，无需凭证、
    不碰 B站，纯前端验证零依赖。

启动：
    python dev/stub_fetcher.py
默认监听 http://127.0.0.1:8899，实现：
    GET /health                      -> {"status":"ok"}
    GET /fetch/bili-history-realtime -> 触发增量（桩：直接返回 started）
    GET /fetch/bili-history          -> 触发全量（桩：直接返回 started）
前端按钮经 server.py(/api/fetcher-trigger) 转发到此桩，即可完整验证闭环。

注意：桩不会真正拉取 B站数据，也不会改变 Analyzer 产物库；它只用于验证「点击按钮
→ server 转发 → 收到响应 → 前端 toast + 3 秒后自动刷新列表」这条控制流。
"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8899


class _H(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/health":
            self._send({"status": "ok", "stub": True, "ts": int(time.time())})
        elif p == "/fetch/bili-history-realtime":
            self._send({"status": "started", "mode": "realtime", "stub": True,
                        "note": "桩：未真正拉取 B站，仅用于验证前端触发闭环"})
        elif p == "/fetch/bili-history":
            self._send({"status": "started", "mode": "full", "stub": True,
                        "note": "桩：未真正拉取 B站，仅用于验证前端触发闭环"})
        else:
            self._send({"error": "not found", "stub": True}, 404)

    def log_message(self, *a):  # 静默访问日志
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), _H)
    print(f"[stub_fetcher] 模拟 Analyzer/Fetcher 控制后端已启动: http://localhost:{PORT}")
    print(f"[stub_fetcher] 现在可从网页点击「实时更新」验证触发闭环（无需真实 Analyzer）")
    print(f"[stub_fetcher] 关闭请按 Ctrl+C")
    srv.serve_forever()
