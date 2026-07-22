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
import configparser
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
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "sync_progress.json")
RESULT_FILE = os.path.join(PROJECT_ROOT, "data", "sync_result.json")
FILTERS_INI = os.path.join(PROJECT_ROOT, "data", "filters.ini")
AUTOSKIP_PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "auto_skip_progress.json")

# 同步状态（后台线程写，前端轮询读）
sync_state = {"running": False, "last": None, "started_at": 0}
# 自动跳过应用状态（后台线程写，前端轮询读）
autoskip_state = {"running": False, "last": None, "started_at": 0}

# 自动跳过规则默认值（持久化到 data/filters.ini）
DEFAULT_FILTERS = {
    "business": ["archive", "pgc", "article", "live"],
    "min_duration_min": 0,
    "authors": [],
}

ALL_BUSINESS = ["archive", "pgc", "article", "live"]


def load_filters():
    """读取自动跳过规则（data/filters.ini），缺省回退 DEFAULT_FILTERS。"""
    filters = {
        "business": list(DEFAULT_FILTERS["business"]),
        "min_duration_min": DEFAULT_FILTERS["min_duration_min"],
        "authors": list(DEFAULT_FILTERS["authors"]),
    }
    if os.path.exists(FILTERS_INI):
        try:
            cfg = configparser.ConfigParser()
            cfg.read(FILTERS_INI, encoding="utf-8")
            if cfg.has_section("filters"):
                b = cfg.get("filters", "business", fallback="")
                if b.strip():
                    filters["business"] = [x.strip() for x in b.split(",") if x.strip()]
                md = cfg.get("filters", "min_duration_min", fallback="0").strip()
                try:
                    filters["min_duration_min"] = int(md) if md else 0
                except ValueError:
                    filters["min_duration_min"] = 0
                a = cfg.get("filters", "authors", fallback="")
                if a.strip():
                    filters["authors"] = [x.strip() for x in a.split(",") if x.strip()]
        except Exception:
            pass
    # 兜底：business 不能空（空则视为全部）
    if not filters["business"]:
        filters["business"] = list(DEFAULT_FILTERS["business"])
    return filters


def save_filters(filters):
    """把自动跳过规则写入 data/filters.ini（条件被修改即持久化）。"""
    cfg = configparser.ConfigParser()
    cfg["filters"] = {
        "business": ",".join(filters.get("business", DEFAULT_FILTERS["business"])),
        "min_duration_min": str(int(filters.get("min_duration_min", 0) or 0)),
        "authors": ",".join(filters.get("authors", []) or []),
    }
    os.makedirs(os.path.dirname(FILTERS_INI), exist_ok=True)
    tmp = FILTERS_INI + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        cfg.write(f)
    os.replace(tmp, FILTERS_INI)


def _write_json_file(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _job_running():
    """是否有同步或自动跳过任务在跑（用于并发互斥，避免同时写库）。"""
    return sync_state["running"] or autoskip_state["running"]


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


def get_sync_meta():
    """读取数据版本 / 最后成功同步时间 / 总条数，供前端常驻文字展示。"""
    meta = {"version": None, "last_success_at": None, "total": 0}
    db = get_db_path()
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='sync_version'").fetchone()
            if row:
                meta["version"] = int(row[0])
            row = conn.execute("SELECT value FROM meta WHERE key='last_success_at'").fetchone()
            if row:
                meta["last_success_at"] = int(row[0])
            row = conn.execute("SELECT COUNT(*) FROM history").fetchone()
            meta["total"] = row[0]
        finally:
            conn.close()
    return meta


def read_progress_file():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def bump_sync_meta():
    """同步成功后递增数据版本并记录最后成功时间。"""
    db = get_db_path()
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='sync_version'").fetchone()
        ver = int(row[0]) if row else 0
        ver += 1
        now = int(time.time())
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('sync_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(ver),),
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('last_success_at',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(now),),
        )
        conn.commit()
    finally:
        conn.close()


def ensure_initial_meta():
    """服务启动时，若已有数据但无版本记录，把初始载入记为 v1。"""
    db = get_db_path()
    if not os.path.exists(db):
        return
    conn = sqlite3.connect(db)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        has_ver = conn.execute("SELECT 1 FROM meta WHERE key='sync_version'").fetchone()
        if cnt > 0 and not has_ver:
            conn.execute("INSERT INTO meta(key,value) VALUES('sync_version','1')")
            lr = conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
            if lr:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES('last_success_at',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (lr[0],),
                )
            conn.commit()
    finally:
        conn.close()


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
        # 「需要观看」= 未完成 且 未被规则自动跳过（auto_skip=0）。
        # 手动跳过（manual_skip=1）保留可见，以便在「需要观看」视图下直接反悔取消（§13.5）；
        # 自动跳过按规则隐藏，需到完整历史或批量「恢复」模式取消。
        # finished = 进度 -1 或 进度 ≥ 95% 时长；NULL 视为未看完
        wheres.append("(auto_skip = 0)")
        wheres.append(
            "NOT (progress = -1 OR "
            "(progress IS NOT NULL AND duration IS NOT NULL AND progress >= 0.95 * duration))"
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
    # 时长筛选（秒）
    dur_min = params.get("duration_min")
    dur_max = params.get("duration_max")
    if dur_min and dur_min.isdigit():
        wheres.append("duration >= ?")
        args.append(int(dur_min))
    if dur_max and dur_max.isdigit():
        wheres.append("duration <= ?")
        args.append(int(dur_max))
    # 设备筛选（dt 值，从 raw_json 提取）[V2]
    dt = (params.get("dt") or "").strip()
    if dt:
        wheres.append("CAST(json_extract(raw_json, '$.history.dt') AS INTEGER) = ?")
        args.append(int(dt))
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
            f"cover, progress, duration, uri, archived_only, manual_skip, auto_skip, "
            f"CASE WHEN manual_skip = 1 THEN 'manual' "
            f"     WHEN auto_skip = 1 THEN 'auto' ELSE '' END AS skip_state, "
            f"json_extract(raw_json, '$.history.dt') AS dt, "
            f"json_extract(raw_json, '$.live_status') AS live_status "
            f"FROM history{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        items = [dict(r) for r in rows]
    finally:
        conn.close()
    return {"items": items, "total": total}


def _has_baseline():
    """是否已有同步基线：last_sync 已记录且库非空 → 可走增量。"""
    db = get_db_path()
    if not os.path.exists(db):
        return False
    conn = sqlite3.connect(db)
    try:
        ls = conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        cnt = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    finally:
        conn.close()
    return bool(ls and cnt > 0)


def run_sync_background(full=False):
    # §12.1：有基线默认增量（快、不重复拉全量）；无基线自动全量建基线；full=强制全量重建
    incremental = (not full) and _has_baseline()
    def _job():
        sync_state["running"] = True
        sync_state["started_at"] = int(time.time())
        # 清掉上一轮完成标记，避免前端误读旧结论
        try:
            if os.path.exists(RESULT_FILE):
                os.remove(RESULT_FILE)
        except Exception:
            pass
        cmd = [sys.executable, os.path.join(HERE, "collector.py")]
        if full:
            cmd.append("--full")
        elif incremental:
            cmd.append("--incremental")
        try:
            subprocess.run(cmd, cwd=PROJECT_ROOT, timeout=900)
            # 以 collector 写出的完成结论为准，而非子进程退出码
            # （中途 break 也会 returncode=0，必须区分「完整完成」与「部分/失败」）
            res = None
            if os.path.exists(RESULT_FILE):
                try:
                    with open(RESULT_FILE, "r", encoding="utf-8") as f:
                        res = json.load(f)
                except Exception:
                    res = None
            completed = bool(res and res.get("completed"))
            if completed:
                bump_sync_meta()  # 只有真正完整拉取才更新数据版本
                sync_state["last"] = {
                    "ok": True,
                    "at": int(time.time()),
                    "fetched": (res or {}).get("fetched"),
                    "mode": "full" if full else ("incremental" if incremental else "auto"),
                }
            else:
                reason = (res or {}).get("error") or "同步未完成"
                sync_state["last"] = {"ok": False, "at": int(time.time()), "err": reason}
        except subprocess.TimeoutExpired:
            sync_state["last"] = {"ok": False, "at": int(time.time()), "err": "同步超时（>900s）"}
        except Exception as e:  # noqa
            sync_state["last"] = {"ok": False, "at": int(time.time()), "err": str(e)}
        finally:
            sync_state["running"] = False

    t = threading.Thread(target=_job, daemon=True)
    t.start()


def apply_autoskip_background():
    """按当前筛选规则对全量数据重扫 auto_skip（§13）。

    - 匹配规则（任一满足即自动跳过）：类型不在 business 白名单 / 时长 ≥ min_duration_min /
      UP主在 authors 名单。
    - 更新规则：匹配 → auto_skip=1 且清除 manual_skip（三态互斥，auto 覆盖 manual/空）；
      不匹配 → auto_skip=0（保留 manual_skip 不变）。
    - 带进度屏蔽：运行期间 autoskip_state.running=True，前端禁用按钮并轮询进度；
      与同步任务互斥（避免同时写库）。
    """
    def _job():
        autoskip_state["running"] = True
        autoskip_state["started_at"] = int(time.time())
        _write_json_file(AUTOSKIP_PROGRESS_FILE, {
            "phase": "start", "done": 0, "total": 0, "completed": False,
            "updated_at": int(time.time()),
        })
        try:
            f = load_filters()
            business_set = set(f["business"])
            min_dur = int(f["min_duration_min"] or 0)
            authors_set = set(f["authors"] or [])
            db = get_db_path()
            conn = sqlite3.connect(db)
            try:
                total = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
                rows = conn.execute(
                    "SELECT kid, business, duration, author_name, manual_skip, auto_skip "
                    "FROM history"
                ).fetchall()
                done = 0
                auto_set = 0
                manual_cleared = 0
                for (kid, business, duration, author_name, ms, as_) in rows:
                    match = False
                    if business and business_set and business not in business_set:
                        match = True
                    if min_dur and duration and duration >= min_dur * 60:
                        match = True
                    if authors_set and author_name and author_name in authors_set:
                        match = True
                    if match:
                        if as_ != 1 or ms != 0:
                            conn.execute(
                                "UPDATE history SET auto_skip=1, manual_skip=0 WHERE kid=?",
                                (kid,),
                            )
                            auto_set += 1
                            if ms == 1:
                                manual_cleared += 1
                    else:
                        if as_ != 0:
                            conn.execute(
                                "UPDATE history SET auto_skip=0 WHERE kid=?", (kid,)
                            )
                    done += 1
                    if done % 200 == 0:
                        conn.commit()
                        _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                            "phase": "apply", "done": done, "total": total,
                            "completed": False, "updated_at": int(time.time()),
                        })
                conn.commit()
                autoskip_state["last"] = {
                    "ok": True, "at": int(time.time()),
                    "auto_set": auto_set, "manual_cleared": manual_cleared, "total": total,
                }
                _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                    "phase": "done", "done": done, "total": total,
                    "completed": True, "auto_set": auto_set,
                    "updated_at": int(time.time()),
                })
            finally:
                conn.close()
        except Exception as e:  # noqa
            autoskip_state["last"] = {"ok": False, "at": int(time.time()), "err": str(e)}
            _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                "phase": "error", "done": 0, "total": 0,
                "completed": False, "error": str(e), "updated_at": int(time.time()),
            })
        finally:
            autoskip_state["running"] = False

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
                "progress": read_progress_file(),
                "last": sync_state["last"],
                "started_at": sync_state["started_at"],
                "meta": get_sync_meta(),
            })
            return
        if path == "/api/filters":
            self._send(200, load_filters())
            return
        if path == "/api/auto-skip-status":
            prog = None
            if os.path.exists(AUTOSKIP_PROGRESS_FILE):
                try:
                    with open(AUTOSKIP_PROGRESS_FILE, "r", encoding="utf-8") as f:
                        prog = json.load(f)
                except Exception:
                    prog = None
            self._send(200, {
                "running": autoskip_state["running"],
                "progress": prog,
                "last": autoskip_state["last"],
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
        qs = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/sync":
            full = qs.get("full", ["0"])[0] == "1"
            if sync_state["running"] or autoskip_state["running"]:
                self._send(200, {"started": False, "blocked": True,
                                 "reason": "已有同步或自动跳过任务进行中"})
            else:
                # 重置进度文件，避免前端残留上一轮的「完成」状态
                try:
                    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                        json.dump({
                            "phase": "start", "page": 0, "fetched": 0,
                            "completed": False, "is_end": False,
                            "total_estimate": None, "updated_at": int(time.time()),
                            "mode": "full" if full else None,
                        }, f)
                except Exception:
                    pass
                run_sync_background(full=full)
                self._send(200, {"started": True, "running": True})
            return
        if parsed.path == "/api/filters":
            # 持久化自动跳过规则（条件被修改即写入 ini）
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except Exception:
                body = {}
            filters = {
                "business": [str(b) for b in (body.get("business") or DEFAULT_FILTERS["business"])],
                "min_duration_min": int(body.get("min_duration_min") or 0),
                "authors": [str(a).strip() for a in (body.get("authors") or []) if str(a).strip()],
            }
            if not filters["business"]:
                filters["business"] = list(DEFAULT_FILTERS["business"])
            save_filters(filters)
            self._send(200, {"ok": True, "filters": filters})
            return
        if parsed.path == "/api/apply-autoskip":
            # 按当前筛选规则全量重扫 auto_skip；带进度屏蔽、与同步互斥
            if sync_state["running"] or autoskip_state["running"]:
                self._send(200, {"started": False, "blocked": True,
                                 "reason": "已有同步或自动跳过任务进行中，请稍后再试"})
            else:
                apply_autoskip_background()
                self._send(200, {"started": True, "running": True})
            return
        if parsed.path == "/api/skip":
            kid = qs.get("kid", [""])[0]
            val = 1 if qs.get("value", ["1"])[0] in ("1", "true", "on") else 0
            if kid:
                db = get_db_path()
                if os.path.exists(db):
                    conn = sqlite3.connect(db)
                    try:
                        if val:
                            # 手动标记：置 manual_skip=1 并清除 auto_skip（三态互斥）
                            conn.execute(
                                "UPDATE history SET manual_skip=1, auto_skip=0 WHERE kid=?",
                                (kid,),
                            )
                        else:
                            conn.execute(
                                "UPDATE history SET manual_skip=0 WHERE kid=?", (kid,)
                            )
                        conn.commit()
                    finally:
                        conn.close()
                    self._send(200, {"ok": True, "kid": kid, "value": val})
                    return
            self._send(400, {"error": "invalid kid"})
            return
        self._send(404, {"error": "not found"})


def main():
    port = get_web_port()
    # 确保已存在数据库的结构与新代码一致（迁移 auto_skip 等新列；不创建空白库）
    db = get_db_path()
    if os.path.exists(db):
        try:
            collector.init_db(db).close()
        except Exception:
            pass
    ensure_initial_meta()
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
