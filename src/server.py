#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站历史记录查看器 — Phase 2 本地 Web 服务（零依赖：仅标准库）

提供：
- 类 B站历史页（封面 + 标题 + UP主 + 时间 + BV，可点击）
- 关键词搜索 + 类型筛选 + 时间范围筛选
- 「需要观看」开关（progress/duration < 95%）
- 封面本地缓存（仅缺文件时下载）
- 一键同步（后台调用 collector 重新拉取，全量以捕获删除/封面）

用法：
    python server.py              # 启动 http://127.0.0.1:8765
    python server.py --port 9000
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector  # 复用 DEFAULT_DB / DEFAULT_CONFIG / UA / REFERER

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
PROJECT_ROOT = collector.PROJECT_ROOT
COVERS_DIR = os.path.join(PROJECT_ROOT, "data", "covers")

# 同步状态（后台线程写，前端轮询读）
sync_state = {"running": False, "last": None, "started_at": 0}


def get_db_path():
    try:
        cfg = collector.load_config(collector.DEFAULT_CONFIG)
    except Exception:
        cfg = {}
    db = cfg.get("db_path") or collector.DEFAULT_DB
    if not os.path.isabs(db):
        db = os.path.join(PROJECT_ROOT, db)
    return db


def get_web_port():
    try:
        cfg = collector.load_config(collector.DEFAULT_CONFIG)
        return int(cfg.get("web_port", 8765))
    except Exception:
        return 8765


def human_dt(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def cover_ext(url):
    if not url:
        return "jpg"
    p = urllib.parse.urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if p.endswith(ext):
            return "jpg" if ext == ".jpeg" else ext.lstrip(".")
    return "jpg"


def ensure_cover(kid, url):
    """返回本地封面文件路径；不存在则从远程下载并缓存。"""
    os.makedirs(COVERS_DIR, exist_ok=True)
    ext = cover_ext(url)
    path = os.path.join(COVERS_DIR, f"{kid}.{ext}")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    if not url:
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": collector.UA, "Referer": collector.REFERER}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception:
        return None


def build_query(params):
    wheres = []
    args = []
    q = (params.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        wheres.append("(title LIKE ? OR author_name LIKE ? OR bvid LIKE ? OR kid LIKE ?)")
        args.extend([like, like, like, like])
    business = (params.get("business") or "").strip()
    if business:
        wheres.append("business = ?")
        args.append(business)
    if params.get("needs_watching") == "1":
        wheres.append(
            "progress IS NOT NULL AND duration IS NOT NULL "
            "AND progress >= 0 AND progress < 0.95 * duration"
        )
    if params.get("archived_only") == "1":
        wheres.append("archived_only = 1")
    date_from = params.get("date_from")
    date_to = params.get("date_to")
    if date_from and date_from.isdigit():
        wheres.append("view_at >= ?")
        args.append(int(date_from))
    if date_to and date_to.isdigit():
        wheres.append("view_at <= ?")
        args.append(int(date_to))
    where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
    order = "view_at DESC"
    return where_sql, args, order


def fetch_history(params):
    db = get_db_path()
    if not os.path.exists(db):
        return {"items": [], "total": 0, "error": "数据库不存在，请先运行 collector.py 拉取"}
    limit = int(params.get("limit") or 60)
    offset = int(params.get("offset") or 0)
    where_sql, args, order = build_query(params)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM history{where_sql}", args
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT kid, title, author_name, author_mid, view_at, bvid, business, "
            f"cover, progress, duration, uri, archived_only "
            f"FROM history{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    return {"items": items, "total": total}


def run_sync_background():
    def _job():
        sync_state["running"] = True
        sync_state["started_at"] = int(time.time())
        try:
            subprocess.run(
                [sys.executable, os.path.join(HERE, "collector.py")],
                cwd=PROJECT_ROOT,
                timeout=600,
            )
            sync_state["last"] = {"ok": True, "at": int(time.time())}
        except Exception as e:  # noqa
            sync_state["last"] = {"ok": False, "at": int(time.time()), "err": str(e)}
        finally:
            sync_state["running"] = False

    t = threading.Thread(target=_job, daemon=True)
    t.start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默默认访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            self._send(404, {"error": "not found"})
            return
        self._send(200, data, ctype)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        flat = {k: (v[0] if v else "") for k, v in qs.items()}

        if path in ("/", "/index.html"):
            self._serve_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            name = os.path.basename(path)
            fpath = os.path.join(WEB_DIR, name)
            if name.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            elif name.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            else:
                ctype = "application/octet-stream"
            self._serve_file(fpath, ctype)
            return
        if path == "/api/history":
            self._send(200, fetch_history(flat))
            return
        if path == "/api/sync":
            self._send(200, {
                "running": sync_state["running"],
                "last": sync_state["last"],
                "started_at": sync_state["started_at"],
            })
            return
        if path.startswith("/cover/"):
            kid = urllib.parse.unquote(path[len("/cover/"):])
            db = get_db_path()
            url = None
            if os.path.exists(db):
                conn = sqlite3.connect(db)
                try:
                    row = conn.execute(
                        "SELECT cover FROM history WHERE kid=?", (kid,)
                    ).fetchone()
                    if row:
                        url = row[0]
                finally:
                    conn.close()
            local = ensure_cover(kid, url) if url else None
            if local and os.path.exists(local):
                ext = os.path.splitext(local)[1].lower()
                ctype = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp",
                    ".gif": "image/gif",
                }.get(ext, "image/jpeg")
                self._serve_file(local, ctype)
            else:
                self._send(404, {"error": "cover not available"})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/sync":
            if sync_state["running"]:
                self._send(200, {"started": False, "running": True})
            else:
                run_sync_background()
                self._send(200, {"started": True, "running": True})
            return
        self._send(404, {"error": "not found"})


def main():
    port = get_web_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[B站历史查看器] 已启动: http://127.0.0.1:{port}")
    print(f"[B站历史查看器] 数据库: {get_db_path()}")
    print(f"[B站历史查看器] 按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
