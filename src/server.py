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
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import datetime
import urllib.error
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
    # 环境变量优先：便于在不干扰在跑实例的前提下另起一个测试实例（BHF_PORT=8799）
    _env = os.environ.get("BHF_PORT")
    if _env:
        try:
            return int(_env)
        except ValueError:
            pass
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


# ============ 网页可控：⑥ 应用变更（自动判定热重载 / 重启）+ ⑦ 底层入口 ============

RESTART_EXIT_CODE = 42      # 退出码哨兵：start.bat 收到 42 会重新拉起服务
_RELOAD_LOCK = threading.Lock()

# 实例身份：每次进程启动唯一。前端靠 boot_id 变化判定"新进程真的回来了"。
# 用 os.urandom 而非 uuid：uuid 会连带引入 _uuid 模块（实测 +0.55 MB），此处无需那种强度。
BOOT_ID = os.urandom(4).hex()
BOOT_TS = int(time.time())
# ② 防重启风暴：重启时间戳记账（data/ 已被 .gitignore 忽略）
RUN_DIR = os.path.join(PROJECT_ROOT, "data", "run")
RESTARTS_FILE = os.path.join(RUN_DIR, "restarts.json")
RESTART_WINDOW_S = 120      # 统计窗口
RESTART_LIMIT = 3           # 窗口内允许的重启次数上限


def _is_supervised():
    """是否由 start.bat 的 supervisor 循环托管（决定重启后能否自动拉起）。"""
    return os.environ.get("BHF_SUPERVISED") == "1"


def _file_meta(path):
    try:
        st = os.stat(path)
        return {"path": path, "mtime": int(st.st_mtime), "size": st.st_size}
    except OSError:
        return None


def hot_reload():
    """底层热重载：重导 engine/store + 重读源库 + 重建封面缓存。

    ✅ 覆盖：engine.py / store.py / rules.json / 数据源库变化
    ❌ 不覆盖：server.py 自身（HTTP 路由与 Handler 类已绑定）→ 需重启进程
    不会重启进程、不释放端口，耗时通常在几十毫秒内。
    """
    with _RELOAD_LOCK:
        t0 = time.time()
        importlib.invalidate_caches()
        importlib.reload(engine)
        importlib.reload(store)
        ensure_rules()
        reload_all()
        try:
            st = rules_status()
        except Exception as e:  # noqa
            st = {"error": str(e)}
        return {
            "ok": True,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "records": len(store.get_raw()),
            "rules_status": st,
            "files": {
                "engine.py": _file_meta(engine.__file__),
                "store.py": _file_meta(store.__file__),
                "rules.json": _file_meta(RULES_FILE),
            },
            "covered": ["engine.py", "store.py", "rules.json", "数据源库(只读重读)"],
            "not_covered": ["server.py", "start.bat", "web/* (浏览器刷新即可)"],
            "rebaselined": _rebaseline(),      # reload/static 类已生效 → 基线推进，徽章清零
            "note": "热重载不重启进程、不释放 8765；日常请直接用「⑥ 应用变更」（会自动判定）。",
        }


def _restart_after_response(httpd, delay=0.6):
    """先让 HTTP 响应发完，再关监听并按哨兵码退出；由 start.bat supervisor 重新拉起。"""
    time.sleep(delay)
    print(f"[B站历史查看器] 收到网页重启请求 → 释放端口并以退出码 {RESTART_EXIT_CODE} 退出，"
          f"等待 supervisor 拉起…", flush=True)
    try:
        httpd.shutdown()        # 停止 serve_forever（须在非 serve_forever 线程调用）
        httpd.server_close()    # 释放监听套接字，避免与新实例双绑定
    except Exception:
        pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except Exception:
            pass
    os._exit(RESTART_EXIT_CODE)


def request_restart(httpd, dry=False):
    """⑦ 重启服务。dry=True 仅回报"托管状态"不真重启（供前端二次确认）。"""
    info = {
        "ok": True,
        "pid": os.getpid(),
        "port": get_web_port(),
        "supervised": _is_supervised(),
        "exit_code": RESTART_EXIT_CODE,
        "dry": bool(dry),
        "restarting": False,
    }
    if not _is_supervised():
        info["warning"] = ("未检测到 supervisor（BHF_SUPERVISED=1）：本次重启后服务不会自动拉起，"
                           "需手动运行 start.bat（或直接 python src/server.py）。")
    if dry:
        return info
    info["restarting"] = True
    threading.Thread(target=_restart_after_response, args=(httpd,), daemon=True).start()
    return info


# ============ ⑥ 单入口「应用变更」：自动判定该热重载还是重启 ============
#   类别 restart = 进程级代码（改了必须重启，热重载覆盖不到）
#        reload  = 数据层 / 配置（importlib.reload 或重读文件即可，0.06~0.3s）
#        static  = 前端静态资源（文件放行即生效，浏览器刷新即可，服务端无需动作）

def _watch_files():
    """扫描出待监视文件 → {相对路径: 类别}。

    用目录扫描而非硬编码清单：以后新增 src/*.py 或 src/web/* 会被自动纳入，不会漏项。
    """
    items = {}
    for rel in ("src/server.py", "start.bat", "stop.bat"):
        items[rel] = "restart"
    for rel in ("src/engine.py", "src/store.py", "src/collector.py",
                "src/adapter_analyzer.py", "data/rules.json", "data/fetcher_config.json"):
        items[rel] = "reload"
    try:
        for name in os.listdir(os.path.join(PROJECT_ROOT, "src")):
            if name.endswith(".py"):
                items.setdefault("src/" + name, "reload")   # 新增模块兜底算热重载
    except OSError:
        pass
    try:
        for name in sorted(os.listdir(os.path.join(PROJECT_ROOT, "src", "web"))):
            if name.lower().endswith((".js", ".html", ".css", ".svg", ".ico")):
                items["src/web/" + name] = "static"
    except OSError:
        pass
    return items


def _code_snapshot():
    """当前磁盘上被监视文件的 (mtime_ns, size) 指纹。"""
    snap = {}
    for rel, kind in _watch_files().items():
        p = os.path.join(PROJECT_ROOT, rel.replace("/", os.sep))
        try:
            st = os.stat(p)
        except OSError:
            continue
        snap[rel] = {"kind": kind, "mtime_ns": st.st_mtime_ns, "size": st.st_size}
    return snap


CODE_BASELINE = _code_snapshot()   # 基线 = 最近一次"已生效"的文件指纹
#   语义：启动时 = 磁盘快照；热重载成功后，reload/static 类会推进到当前磁盘状态
#   （否则徽章会把"已经生效的改动"永远算作待应用）。restart 类只由重启刷新。


def _code_version(snap=None):
    """③ 版本指纹：所有被监视文件 mtime+size 的稳定短摘要。

    手写 FNV-1a 64 而不用 hashlib：hashlib 会连带加载 _hashlib/_blake2（实测 +1.61 MB），
    而这里只需要一个跨进程稳定、可比较的短标识（不能用内置 hash()，它对字符串每次随机）。
    """
    snap = CODE_BASELINE if snap is None else snap
    acc = 0xCBF29CE484222325
    for rel in sorted(snap):
        e = snap[rel]
        for ch in f"{rel}:{e['mtime_ns']}:{e['size']}":
            acc = ((acc ^ ord(ch)) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{acc:016x}"[:10]


def _diff_code(cur=None):
    """当前磁盘 vs 启动快照 → {"restart": [...], "reload": [...], "static": [...]}。"""
    cur = _code_snapshot() if cur is None else cur
    out = {"restart": [], "reload": [], "static": []}
    for rel, e in cur.items():
        old = CODE_BASELINE.get(rel)
        changed = old is None or old["mtime_ns"] != e["mtime_ns"] or old["size"] != e["size"]
        if changed:
            # 新增文件 → 保守归为 restart（最保险：不会漏掉未被 reload 覆盖的改动）
            out.setdefault(e["kind"] if old is not None else "restart", []).append(rel)
    for rel in CODE_BASELINE:
        if rel not in cur:
            out["restart"].append(rel + " (已删除)")
    return {k: sorted(v) for k, v in out.items()}


def _rebaseline(kinds=("reload", "static")):
    """把"已生效"类别的基线推进到当前磁盘状态（用于徽章清零）。

    reload 类一旦热重载成功、static 类一旦被文件服务读到，就算"已生效"，不再算待应用；
    restart 类（server.py / start.bat 等）不在此列——只有重启进程才能真正刷新。
    """
    cur = _code_snapshot()
    n = 0
    for rel, e in cur.items():
        if e["kind"] in kinds and CODE_BASELINE.get(rel) != e:
            CODE_BASELINE[rel] = e
            n += 1
    return n


def _read_restarts():
    try:
        with open(RESTARTS_FILE, "r", encoding="utf-8") as f:
            return [float(x) for x in (json.load(f).get("restarts") or [])]
    except Exception:
        return []


def _restart_guard():
    """② 防重启风暴：窗口内重启次数是否已达上限 → (allowed, recent_count, retry_after_s)。"""
    now = time.time()
    recent = [t for t in _read_restarts() if now - t < RESTART_WINDOW_S]
    if len(recent) >= RESTART_LIMIT:
        return False, len(recent), int(RESTART_WINDOW_S - (now - min(recent))) + 1
    return True, len(recent), 0


def _note_restart():
    now = time.time()
    recent = [t for t in _read_restarts() if now - t < RESTART_WINDOW_S] + [now]
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(RESTARTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"restarts": recent[-20:], "updated_at": int(now)}, f)
    except Exception:
        pass


def code_status():
    """① 只读：启动快照指纹 / 磁盘指纹 / 待应用变更 / 建议动作 / 重启护栏状态。"""
    _cur = _code_snapshot()          # 只扫一次磁盘，给 diff 与指纹复用
    d = _diff_code(_cur)
    pending = len(d["restart"]) + len(d["reload"]) + len(d["static"])
    allowed, recent, retry_after = _restart_guard()
    return {
        "ok": True,
        "boot_id": BOOT_ID,
        "pid": os.getpid(),
        "port": get_web_port(),
        "booted_at": BOOT_TS,
        "supervised": _is_supervised(),
        "version": _code_version(),                           # 启动时指纹
        "current_version": _code_version(_cur),                # 磁盘当前指纹
        "changes": d,
        "pending": pending,
        "next_action": ("restart" if d["restart"] else
                        "reload" if d["reload"] else
                        "static" if d["static"] else "none"),
        "restart_guard": {"window_s": RESTART_WINDOW_S, "limit": RESTART_LIMIT,
                          "recent": recent, "allowed": allowed, "retry_after_s": retry_after},
    }


def apply_changes(httpd, force=None):
    """① 单入口：自动判定 → restart / reload / static / none / manual / blocked。

    force="reload" 强制热重载；force="restart" 强制重启（仍受 ② 护栏与托管检查约束）。
    """
    d = _diff_code()
    pending = len(d["restart"]) + len(d["reload"]) + len(d["static"])
    info = {
        "ok": True, "boot_id": BOOT_ID, "pid": os.getpid(),
        "changes": d, "pending": pending, "force": force or None,
        "version": _code_version(), "action": None,
    }
    if force is None and not pending:
        info["action"] = "none"
        info["hint"] = "未检测到任何变更，无需操作。"
        return info

    if force == "restart":
        do_restart = True
    elif force == "reload":
        do_restart = False
        if d["restart"]:
            info["warning"] = "以下改动需要重启进程才能生效，本次仅热重载：" + "、".join(d["restart"])
    else:
        do_restart = bool(d["restart"])

    if do_restart:
        allowed, recent, retry_after = _restart_guard()
        if not allowed:
            info["action"] = "blocked"
            info["blocked_reason"] = (
                f"{RESTART_WINDOW_S} 秒内已重启 {recent} 次（上限 {RESTART_LIMIT}），"
                f"为防重启风暴本次拒绝；约 {retry_after} 秒后可再试，或手动重开 start.bat。")
            return info
        if not _is_supervised():
            info["action"] = "manual"
            info["warning"] = ("未检测到 supervisor（BHF_SUPERVISED=1）：重启后服务不会自动拉起，"
                               "请用 start.bat 启动。已尽力热重载可覆盖的部分。")
            try:
                r = hot_reload()
                info["partial_reload"] = {"ok": r.get("ok"), "records": r.get("records"),
                                          "elapsed_ms": r.get("elapsed_ms")}
            except Exception as e:  # noqa
                info["partial_reload"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            return info
        _note_restart()
        info["action"] = "restart"
        info["restarting"] = True
        threading.Thread(target=_restart_after_response, args=(httpd,), daemon=True).start()
        return info

    if not d["reload"] and d["static"]:
        # 只改了前端静态文件：文件放行即生效，服务端什么都不用做
        info["action"] = "static"
        info["static_pending"] = d["static"]
        info["rebaselined"] = _rebaseline(("static",))
        info["hint"] = "仅前端静态文件有变更：浏览器 Ctrl+F5 刷新即可，服务端无需动作。"
        return info

    r = hot_reload()
    info["action"] = "reload"
    info["reload"] = {k: r.get(k) for k in ("ok", "elapsed_ms", "records", "files")}
    if d["static"]:
        info["static_pending"] = d["static"]
    info["hint"] = ("已热重载数据层/规则；" +
                    ("另有前端静态文件变更，浏览器 Ctrl+F5 刷新即可。" if d["static"]
                     else "无需重启。"))
    return info


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


# ===================== Fetcher/Analyzer 控制后端转发（Phase 1.5） =====================
# Analyzer 的「控制大脑」是 BilibiliHistoryFetcher 后端（默认 http://localhost:8899）。
# 本服务仅做只读转发 + 优雅降级；不持有任何凭证（SESSDATA 在 Fetcher 侧读取）。

def _fetcher_cfg():
    """返回 (base_url, api_key)。优先级：运行时覆盖 > 环境变量 > data/config.json[fetcher] > 默认值。"""
    base = FETCHER_OVERRIDE.get("base") or os.environ.get("FETCHER_BASE", "")
    key = FETCHER_OVERRIDE.get("key") or os.environ.get("FETCHER_KEY", "")
    if not base:
        try:
            cfg = collector.load_config(collector.DEFAULT_CONFIG) or {}
            fet = (cfg.get("fetcher") if isinstance(cfg, dict) else None) or {}
            base = fet.get("base") or ""
            key = key or (fet.get("api_key") or "")
        except Exception:
            pass
    if not base:
        base = "http://localhost:8899"
    return base.rstrip("/"), (key or "")


# 运行时覆盖：由前端「设置」面板写入，优先级高于环境变量与 config.json，持久化到本地文件
FETCHER_OVERRIDE = {}

def _fetcher_cfg_path():
    return os.path.join(PROJECT_ROOT, "data", "fetcher_config.json")

def _load_fetcher_override():
    try:
        with open(_fetcher_cfg_path(), "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        FETCHER_OVERRIDE["base"] = d.get("base") or None
        FETCHER_OVERRIDE["key"] = d.get("api_key") or None
    except Exception:
        pass

def _persist_fetcher_override(base, key):
    FETCHER_OVERRIDE["base"] = base or None
    FETCHER_OVERRIDE["key"] = key or None
    try:
        os.makedirs(os.path.dirname(_fetcher_cfg_path()), exist_ok=True)
        with open(_fetcher_cfg_path(), "w", encoding="utf-8") as f:
            json.dump({"base": base or "", "api_key": key or ""}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

_load_fetcher_override()


def _forward_fetcher(rel_path, timeout=30, method="GET", params=None):
    """转发到 Fetcher 后端（Analyzer 控制层），优雅降级：
    连接失败返回 reachable=False 的结构化 dict，绝不抛 500 崩溃。
    method 支持 GET/POST；params 为查询参数 dict（自动拼到 URL）。"""
    base, key = _fetcher_cfg()
    url = base + rel_path
    if params:
        sep = "&" if "?" in url else "?"
        url += sep + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, method=method)
        if key:
            req.add_header("X-API-Key", key)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = raw
        return {"ok": True, "reachable": True, "status": r.status, "data": data}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        return {"ok": False, "reachable": True, "status": e.code,
                "error": f"Fetcher 返回 {e.code}: {body[:200]}"}
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None)
        reason = reason if isinstance(reason, str) else str(e)
        return {"ok": False, "reachable": False,
                "error": f"无法连接 Analyzer/Fetcher 后端（{base}）：{reason}"}


def _analyzer_interaction_status():
    """Step4（数据自检）真正的『Analyzer 交互测试』：探测 Analyzer 是否可达 + 本服务已只读读到的 Analyzer 主源条数。
    与 Finder 自己的 collector（POST /api/sync）完全无关——那部分违反本文件设计（只读转发、不持有凭证），
    其 progress.phase:error/-101 是 Finder 自身 SESSDATA 过期，不是 Analyzer。"""
    health = _forward_fetcher("/health", timeout=5)
    meta = get_sync_meta()
    return {
        "reachable": health.get("reachable", False),
        "health_status": health.get("status"),
        "records_read": meta.get("analyzer", 0),      # 本服务只读读到的 Analyzer 主源条数
        "local_backup": meta.get("local_backup", 0),
    }


def _analyzer_integrity_check():
    """Step④ 数据自检（重新实现）：真正调用 Analyzer 的完整性校验子系统
    POST /data_sync/check（JSON↔DB diff）+ GET /data_sync/report（markdown 报告）。
    与 Finder 自己的 collector（POST /api/sync）完全无关——不读 sync_progress.json，
    因此不再出现陈旧 -101。返回结构化结果供前端展示。"""
    health = _forward_fetcher("/health", timeout=5)
    if not health.get("reachable"):
        return {"ok": False, "reachable": False,
                "error": "Analyzer 不可达，无法执行自检",
                "health": health}
    # 1) 强制跑完整性校验（同步模式，避免异步轮询复杂度）
    chk = _forward_fetcher("/data_sync/check", timeout=90, method="POST",
                           params={"force_check": "true"})
    check = {}
    if chk.get("ok"):
        check = chk.get("data") if isinstance(chk.get("data"), dict) else {"raw": chk.get("data")}
        # 2) 拉取完整性报告（markdown）
        rep = _forward_fetcher("/data_sync/report", timeout=15)
        if rep.get("ok") and isinstance(rep.get("data"), dict):
            check["report"] = rep["data"].get("content")
            check["report_modified"] = rep["data"].get("modified_time")
            check["report_file"] = rep["data"].get("file_path")
    else:
        check = {"check_error": chk.get("error")}
    meta = get_sync_meta()
    return {
        "ok": True,
        "reachable": True,
        "health_status": health.get("status"),
        "records_read": meta.get("analyzer", 0),
        "local_backup": meta.get("local_backup", 0),
        "check": check,
    }


# ===================== 轻量级自主备份（Finder 本地安全副本） =====================
# 定位：Finder 作为只读 viewer，对它所依赖的 Analyzer 主源做一份本地快照，
# 使 Analyzer 宕机/库损坏/库删除时，Finder 仍有可浏览的本地副本（轻量、独立于 Analyzer 的 /data_sync）。
BACKUP_ROOT = os.path.join(store.DATA_DIR, "backup")


def _count_db_records(db_path):
    """统计 DB 记录数：优先 bilibili_history_YYYY 年表，回退 history 单表（本地库）。"""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.cursor()
        total = 0
        for pat in ("bilibili_history_[0-9][0-9][0-9][0-9]", "history"):
            for (tb,) in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB ?",
                (pat,),
            ):
                total += cur.execute(f"SELECT COUNT(*) FROM {tb}").fetchone()[0]
        con.close()
        return total
    except Exception:
        return 0


def _backup_one(src, dst):
    """用 sqlite3 在线 backup 做一致性快照（避免文件锁导致的不一致）。"""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        tgt = sqlite3.connect(dst)
        try:
            con.backup(tgt)
        finally:
            tgt.close()
    finally:
        con.close()


def _create_backup():
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(BACKUP_ROOT, ts)
    os.makedirs(folder, exist_ok=True)
    manifest = {
        "created_at": ts,
        "items": [],
        "analyzer_db": store.ANALYZER_DB if os.path.exists(store.ANALYZER_DB) else None,
    }
    for label, db in (("analyzer", store.ANALYZER_DB), ("local", store.LOCAL_DB)):
        if os.path.exists(db):
            dst = os.path.join(folder, os.path.basename(db))
            _backup_one(db, dst)
            manifest["items"].append({
                "label": label,
                "file": os.path.basename(db),
                "size": os.path.getsize(dst),
                "records": _count_db_records(db),
            })
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def _list_backups():
    if not os.path.isdir(BACKUP_ROOT):
        return []
    out = []
    for name in sorted(os.listdir(BACKUP_ROOT), reverse=True):
        mp = os.path.join(BACKUP_ROOT, name, "meta.json")
        if os.path.exists(mp):
            try:
                out.append(json.load(open(mp, encoding="utf-8")))
            except Exception:
                pass
    return out



class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默默认访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            # 客户端提前断开（浏览器刷新/关闭页面、轮询被取消）会触发 WinError 10053 / BrokenPipe。
            # 这类异常无意义，静默忽略并让本连接关闭，避免 socketserver 打印 traceback 噪声。
            self.close_connection = True

    def handle_one_request(self):
        """容忍客户端在「请求读取阶段」断开（如 Ctrl+F5 打断上一请求），避免异常冒泡到 socketserver。"""
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            self.close_connection = True

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
                "analyzer": _analyzer_interaction_status(),
            })
            return
        if path == "/api/fetcher-health":
            # 探测本机 Analyzer/Fetcher 后端是否可达（8899/health）
            self._send(200, _forward_fetcher("/health", timeout=5))
            return
        if path == "/api/fetcher-trigger":
            # 触发 Analyzer 重新拉取/分析：?mode=full 走全量，默认增量
            mode = flat.get("mode") or ""
            # sync_deleted：默认同步删除记录（保持与 B 站一致）；前端可传 0 跳过
            sync_deleted = flat.get("sync_deleted", "1")
            params = {"sync_deleted": sync_deleted}
            if mode == "full":
                self._send(200, _forward_fetcher("/fetch/bili-history", timeout=240, params=params))
                return
            # 增量：先打 realtime；若 Analyzer 报『未找到本地历史记录』（缺基线），
            # 自动升级为全量——对齐开源 Frontend 的 updateBiliHistoryRealtime 回退策略。
            inc = _forward_fetcher("/fetch/bili-history-realtime", timeout=180, params=params)
            d = inc.get("data") if isinstance(inc.get("data"), dict) else {}
            if inc.get("ok") and d.get("status") == "error" and "未找到本地历史记录" in str(d.get("message", "")):
                full = _forward_fetcher("/fetch/bili-history", timeout=240, params=params)
                self._send(200, {
                    "ok": True,
                    "fallback_to_full": True,
                    "message": "增量拉取缺少本地基线，已自动升级为全量拉取",
                    "incremental": d,
                    "full": full.get("data"),
                })
                return
            self._send(200, inc)
            return
        if path == "/api/fetcher-check":
            # Step④ 数据自检（重新实现）：真正调用 Analyzer 完整性校验 + 报告
            self._send(200, _analyzer_integrity_check())
            return
        if path == "/api/backups":
            self._send(200, {"backups": _list_backups()})
            return
        if path == "/api/fetcher-config":
            # 当前 Fetcher 连接配置（不回传明文 key，只给是否已设置 + 来源）
            base, key = _fetcher_cfg()
            self._send(200, {
                "base": base,
                "has_key": bool(key),
                "source": "override" if FETCHER_OVERRIDE.get("base") else "default",
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
        if path == "/api/code-status":
            # ① 只读：待应用变更 / 启动与磁盘指纹 / 重启护栏状态（前端徽章轮询用）
            self._send(200, code_status())
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

        if parsed.path == "/api/apply":
            # ① 单入口「应用变更」：自动判定热重载 or 重启；?force=reload|restart 为 API 级逃生口
            force = qs.get("force", [""])[0] or (body.get("force") if isinstance(body, dict) else "") or None
            if force not in ("reload", "restart"):
                force = None
            self._send(200, apply_changes(self.server, force=force))
            return

        if parsed.path == "/api/reload":
            # ⑦ 底层入口（保留）：强制热重载，不判定、不重启
            try:
                self._send(200, hot_reload())
            except Exception as e:  # noqa
                self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        if parsed.path == "/api/restart":
            # ⑦ 重启服务：?dry=1 只回报托管状态；真重启需带确认头，防手滑/误触
            dry = qs.get("dry", ["0"])[0] == "1"
            if not dry and self.headers.get("X-BHF-Confirm") != "restart":
                info = request_restart(self.server, dry=True)
                info["refused"] = "缺少确认头 X-BHF-Confirm: restart（本次未重启）"
                self._send(200, info)
                return
            self._send(200, request_restart(self.server, dry=dry))
            return

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

        if parsed.path == "/api/backup":
            # 轻量级自主备份：对 Finder 依赖的 Analyzer 主源 + 本地库做一致快照
            try:
                manifest = _create_backup()
                self._send(200, {"ok": True, "manifest": manifest})
            except Exception as e:  # noqa
                self._send(500, {"ok": False, "error": f"备份失败：{e}"})
            return

        if parsed.path == "/api/fetcher-config":
            # 前端「设置」面板写入：后端地址 + 可选 X-API-Key，持久化并即时生效
            base = (body.get("base") or "").strip()
            key = (body.get("key") or "").strip()
            if not base:
                self._send(400, {"ok": False, "error": "后端地址不能为空"})
                return
            if not base.startswith("http://") and not base.startswith("https://"):
                self._send(400, {"ok": False, "error": "后端地址须以 http:// 或 https:// 开头"})
                return
            _persist_fetcher_override(base, key)
            health = _forward_fetcher("/health", timeout=5)
            self._send(200, {"ok": True, "base": base, "has_key": bool(key), "health": health})
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
                "time_from": body.get("time_from"),
                "time_to": body.get("time_to"),
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
    print(f"[B站历史查看器] 实例: boot_id={BOOT_ID}  pid={os.getpid()}  code={_code_version()}  "
          f"supervised={'yes' if _is_supervised() else 'no'}", flush=True)
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
