#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站历史记录查看器 — Phase 1 本地 Web 服务（续看规则引擎 + Analyzer 全量只读）

提供：
- 类 B站历史页（封面 + 标题 + UP主 + 时间 + BV，可点击）
- 关键词搜索 + 类型筛选 + 时间范围筛选 + 设备筛选
- 「需要观看 / 已跳过 / 已搁置」三视图（基于规则引擎 auto_skip + auto_skip_reason）
- 自定义续看规则引擎（data/rules.json，单条 active + 多预设，含冲突校验）
- 封面本地缓存（仅缺文件时下载）
- 一键同步（后台调用 collector 重新拉取，作 Analyzer 的备份源补充）

Phase 1 关键变化（对应正式版方案 §3.3）：
- 数据源 = Analyzer SQLite（read-only 直连，主源）+ 本地 Finder 库（read-only，备份源）；
- 分类层（auto_skip / manual_skip / auto_exempt）仅落侧状态库 data/canonical_state.db，
  绝不回写 Analyzer / Finder 源库（源库只读原则）；
- 规则引擎取自 engine.py（可移植纯逻辑），store.py 负责多源读取与分类。

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
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector  # 复用 DEFAULT_DB / DEFAULT_CONFIG / UA / REFERER / init_db / load_config
import engine
import store

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
PROJECT_ROOT = collector.PROJECT_ROOT
COVERS_DIR = os.path.join(PROJECT_ROOT, "data", "covers")
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "sync_progress.json")
RESULT_FILE = os.path.join(PROJECT_ROOT, "data", "sync_result.json")
RULES_FILE = os.path.join(PROJECT_ROOT, "data", "rules.json")
AUTOSKIP_PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "auto_skip_progress.json")

# 同步状态（后台线程写，前端轮询读）
sync_state = {"running": False, "last": None, "started_at": 0}
# 自动跳过应用状态（后台线程写，前端轮询读）
autoskip_state = {"running": False, "last": None, "started_at": 0}

ALL_BUSINESS = ["archive", "pgc", "article", "live"]

# 封面 URL 缓存（reload 时重建）：kid -> cover url
COVER_MAP = {}


# ===================== 规则文件 IO（仅文件，语义交 engine） =====================

def ensure_rules():
    """确保 rules.json 存在：首次启动由默认规则生成（遗留 filters.ini 已废弃）。"""
    if os.path.exists(RULES_FILE):
        return
    _write_json_file(RULES_FILE, engine.default_rules())


def load_rules():
    ensure_rules()
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("rules"), list):
            d = engine.default_rules()
    except Exception:
        d = engine.default_rules()
    if not any(r.get("active") for r in d.get("rules", [])):
        if d.get("rules"):
            d["rules"][0]["active"] = True
    return d


def save_rules(rules):
    _write_json_file(RULES_FILE, rules)


def get_active_rule(rules):
    return engine.get_active_rule(rules)


def validate_rule(rule):
    return engine.validate_rule(rule)


# ===================== 既有工具 =====================

def _write_json_file(path, obj):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _job_running():
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


def reload_all():
    """重新读取多源记录并重建封面缓存（启动 / 同步完成后调用）。"""
    store.reload_raw()
    raw = store.get_raw()
    COVER_MAP.clear()
    for r in raw:
        if r.get("cover"):
            COVER_MAP[r.get("kid")] = r["cover"]


def read_progress_file():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def get_sync_meta():
    """读取数据版本 / 最后成功同步时间，并附 Analyzer 合并条数，供前端常驻文字展示。"""
    meta = {"version": None, "last_success_at": None,
            "total": 0, "analyzer": 0, "local_backup": 0}
    banner = store.get_meta_banner(store.get_raw())
    meta["total"] = banner["total"]
    meta["analyzer"] = banner["analyzer"]
    meta["local_backup"] = banner["local_backup"]
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
        finally:
            conn.close()
    return meta


def _rules_hash(rules):
    return engine.rules_hash(rules)


def _store_applied_rules():
    """应用成功后记录『已应用规则哈希』与『最后应用时间』，供前端『待应用/脏』角标。"""
    store.set_meta("rule_applied_hash", _rules_hash(load_rules()))
    store.set_meta("last_rule_apply_at", int(time.time()))


def rules_status():
    rules = load_rules()
    rule = get_active_rule(rules)
    state = store.load_state()
    return store.rules_status(store.get_raw(), rule, state, rules)


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
            res = None
            if os.path.exists(RESULT_FILE):
                try:
                    with open(RESULT_FILE, "r", encoding="utf-8") as f:
                        res = json.load(f)
                except Exception:
                    res = None
            completed = bool(res and res.get("completed"))
            if completed:
                sync_state["last"] = {
                    "ok": True, "at": int(time.time()),
                    "fetched": (res or {}).get("fetched"),
                    "mode": "full" if full else ("incremental" if incremental else "auto"),
                }
                # 同步成功 → 重新读取多源（本地备份源新增了记录）
                try:
                    reload_all()
                except Exception:
                    pass
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


def apply_rules_background(dry=False):
    """按当前 active 规则对全量数据重扫 auto_skip（§14 续看规则引擎）。"""
    def _job():
        autoskip_state["running"] = True
        autoskip_state["started_at"] = int(time.time())
        _write_json_file(AUTOSKIP_PROGRESS_FILE, {
            "phase": "start", "done": 0, "total": 0, "completed": False,
            "dry": dry, "updated_at": int(time.time()),
        })
        try:
            rules = load_rules()
            rule = get_active_rule(rules)
            state = store.load_state()
            res = store.apply_rules(store.get_raw(), rule, state, dry=dry)
            if not res.get("ok"):
                autoskip_state["last"] = {"ok": False, "err": res.get("err", "规则应用失败")}
                _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                    "phase": "error", "done": 0, "total": 0, "completed": False,
                    "error": res.get("err", ""), "updated_at": int(time.time()),
                })
                return
            if dry:
                autoskip_state["last"] = {"ok": True, "dry": True, **res}
                _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                    "phase": "done", "done": res["total"], "total": res["total"],
                    "completed": True, "dry": True, "updated_at": int(time.time()), **res,
                })
            else:
                autoskip_state["last"] = {
                    "ok": True, "at": int(time.time()),
                    "auto_set": res["auto_set"], "stale_set": res["stale_set"],
                    "manual_cleared": res["manual_cleared"], "total": res["total"],
                }
                _store_applied_rules()
                _write_json_file(AUTOSKIP_PROGRESS_FILE, {
                    "phase": "done", "done": res["total"], "total": res["total"],
                    "completed": True, "auto_set": res["auto_set"],
                    "stale_set": res["stale_set"], "manual_cleared": res["manual_cleared"],
                    "updated_at": int(time.time()),
                })
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
            rules = load_rules()
            rule = get_active_rule(rules)
            state = store.load_state()
            items, total, counts = store.query(store.get_raw(), rule, state, flat)
            self._send(200, {"items": items, "total": total, "counts": counts})
            return
        if path == "/api/views":
            self._send(200, {"views": store.list_views()})
            return
        if path == "/api/lists":
            self._send(200, {"lists": store.list_lists()})
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
        if path == "/api/rules":
            self._send(200, load_rules())
            return
        if path == "/api/rules-dry":
            try:
                rules = load_rules()
                rule = get_active_rule(rules)
                state = store.load_state()
                self._send(200, store.apply_rules(store.get_raw(), rule, state, dry=True))
            except Exception as e:  # noqa
                self._send(200, {"ok": False, "err": str(e)})
            return
        if path == "/api/rules-status":
            self._send(200, rules_status())
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
            url = COVER_MAP.get(kid)
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
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}") if raw else {}
        except Exception:
            body = {}

        if parsed.path == "/api/sync":
            full = qs.get("full", ["0"])[0] == "1"
            if sync_state["running"] or autoskip_state["running"]:
                self._send(200, {"started": False, "blocked": True,
                                 "reason": "已有同步或规则应用任务进行中"})
            else:
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

        if parsed.path == "/api/rules":
            # 持久化规则（先校验冲突；再强制单条 active）
            rules = body if isinstance(body, dict) and "rules" in body else None
            if not rules:
                self._send(400, {"ok": False, "error": "无效的规则数据"})
                return
            rule_list = rules.get("rules", [])
            active_count = sum(1 for r in rule_list if r.get("active"))
            if active_count > 1:
                self._send(200, {"ok": False, "conflicts": {
                    "hard": ["存在多条 active 规则，v1 仅允许一条激活（C6）。请先停用其他规则。"],
                    "soft": [], "info": [],
                }})
                return
            if active_count == 0:
                self._send(200, {"ok": False, "conflicts": {
                    "hard": ["没有激活的规则，请至少激活一条（C6）。"],
                    "soft": [], "info": [],
                }})
                return
            all_hard, all_soft = [], []
            for r in rule_list:
                v = validate_rule(r)
                all_hard.extend(v["hard"])
                all_soft.extend(v["soft"])
            if all_hard:
                self._send(200, {"ok": False, "conflicts": {
                    "hard": all_hard, "soft": all_soft, "info": [],
                }})
                return
            save_rules(rules)
            self._send(200, {"ok": True, "rules": load_rules(), "warnings": all_soft})
            return

        if parsed.path == "/api/rules-dry":
            try:
                rules = load_rules()
                rule = get_active_rule(rules)
                state = store.load_state()
                self._send(200, store.apply_rules(store.get_raw(), rule, state, dry=True))
            except Exception as e:  # noqa
                self._send(200, {"ok": False, "err": str(e)})
            return

        if parsed.path == "/api/apply-rules":
            if sync_state["running"] or autoskip_state["running"]:
                self._send(200, {"started": False, "blocked": True,
                                 "reason": "已有同步或规则应用任务进行中，请稍后再试"})
            else:
                apply_rules_background(dry=False)
                self._send(200, {"started": True, "running": True})
            return

        if parsed.path == "/api/skip":
            kid = qs.get("kid", [""])[0]
            val = 1 if qs.get("value", ["1"])[0] in ("1", "true", "on") else 0
            kind = qs.get("kind", ["manual"])[0]  # manual=手动标记；auto=取消自动跳过
            if kid:
                if kind == "auto":
                    store.save_skip(kid, auto_exempt=1)
                else:
                    store.save_skip(kid, manual_skip=val)
                self._send(200, {"ok": True, "kid": kid, "value": val, "kind": kind})
                return
            self._send(400, {"error": "invalid kid"})
            return

        if parsed.path == "/api/query":
            rules = load_rules()
            rule = get_active_rule(rules)
            state = store.load_state()
            params = {
                "view": body.get("view", "all"),
                "q": body.get("q", ""),
                "filter": body.get("filter"),
                "sort": body.get("sort", []),
                "limit": body.get("limit", 60),
                "offset": body.get("offset", 0),
                "lists": body.get("lists", {}),
            }
            items, total, counts = store.query(store.get_raw(), rule, state, params)
            self._send(200, {"items": items, "total": total, "counts": counts})
            return

        if parsed.path == "/api/views":
            if body.get("action") == "delete":
                ok = store.delete_view(body.get("id"))
                self._send(200, {"ok": ok})
                return
            vid = store.save_view(body.get("id"), body.get("name", "未命名视图"),
                                  body.get("spec", {}))
            self._send(200, {"ok": True, "id": vid, "views": store.list_views()})
            return

        if parsed.path == "/api/lists":
            if body.get("action") == "delete":
                ok = store.delete_list(body.get("id"))
                self._send(200, {"ok": ok})
                return
            lid = store.save_list(body.get("id"), body.get("name", "未命名列表"),
                                  body.get("kind", "blacklist"),
                                  body.get("values", []))
            self._send(200, {"ok": True, "id": lid, "lists": store.list_lists()})
            return

        if parsed.path == "/api/batch":
            kids = body.get("kids", [])
            action = body.get("action")
            if not kids or not action:
                self._send(400, {"error": "kids 与 action 必填"})
                return
            affected = store.batch_op(kids, action)
            self._send(200, {"ok": True, "affected": affected})
            return
        self._send(404, {"error": "not found"})


def main():
    port = get_web_port()
    ensure_rules()
    reload_all()
    # 首次启动自动校准一次：若从未应用过规则，按默认规则跑一遍（dry 仅标记应用时间，不写源库）
    if not store.get_meta("rule_applied_hash"):
        try:
            _store_applied_rules()
            print("[B站历史查看器] 首次启动：已按当前规则固化『已应用』状态", flush=True)
        except Exception as e:  # noqa
            print(f"[B站历史查看器] 首次校准失败：{e}", flush=True)
    # 双栈绑定
    server = None
    bind_host = None
    last_err = None
    for _host in ("::", "0.0.0.0", "127.0.0.1"):
        try:
            server = ThreadingHTTPServer((_host, port), Handler)
            bind_host = _host
            break
        except OSError as e:
            last_err = e
            continue
    if server is None:
        e = last_err
        if e.errno in (98, 10048, 48) or getattr(e, "winerror", None) == 10048:
            print(f"[B站历史查看器] 端口 {port} 已被占用 —— 服务很可能已经在运行。", flush=True)
            print(f"                 直接打开 http://127.0.0.1:{port} 即可；", flush=True)
            print(f"                 若想重启，请先结束占用该端口的进程，再重新运行本命令。", flush=True)
        else:
            print(f"[B站历史查看器] 无法绑定端口 {port}：{e}", flush=True)
        sys.exit(1)
    banner = store.get_meta_banner(store.get_raw())
    print(f"[B站历史查看器] 已启动: http://127.0.0.1:{port}  (也可访问 http://localhost:{port})", flush=True)
    print(f"[B站历史查看器] 数据源: Analyzer(主,只读)={banner['analyzer']} 条 + "
          f"本地Finder(备份,只读)={banner['local_backup']} 条 → 合并 {banner['total']} 条", flush=True)
    print(f"[B站历史查看器] Analyzer 库: {banner['analyzer_db']}", flush=True)
    try:
        st = rules_status()
        print(f"[B站历史查看器] 规则状态: 待应用(pending)={st.get('pending')}  自上次应用以来新增未套用(dirty_count)={st.get('dirty_count')}", flush=True)
    except Exception:
        pass
    print(f"[B站历史查看器] 按 Ctrl+C 停止（关闭本窗口也会停止服务）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
