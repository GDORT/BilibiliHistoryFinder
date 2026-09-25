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
import shutil
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
    """监听端口：只读 config.json 的 web_port，缺省 8765。

    历史上曾支持 `BHF_PORT` 环境变量覆盖（当初为「在不干扰在跑实例的前提下
    另起一个测试实例」而加），**2026-09-25 深夜已回退移除** —— 端口只由配置
    文件决定，不再受环境变量影响，避免环境里残留的值把服务引到别的端口。
    """
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
# 与 start.bat 的「文件握手」：进程退出前若留下它，supervisor 就无条件重新拉起，
# 不再依赖 %errorlevel%（退出码在 Windows 上会被若干因素吞掉，见下方注释）。
RELAUNCH_FLAG = os.path.join(RUN_DIR, "relaunch.flag")
# 重启请求挂起标记 —— **由主线程**在 serve_forever 返回后据此决定退出码。
RESTART_PENDING = {"requested": False, "at": 0.0, "reason": ""}
RESTART_LOG = os.path.join(RUN_DIR, "restart.log")   # 重启链路留痕（只写文件，不写控制台）
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


# ============ 控制台健壮性：为什么"打日志"会把重启卡死（2026-09-25 实测根因）============
# Windows 控制台处于 QuickEdit「快速编辑」**选中**状态时，任何进程向该控制台写
# stdout/stderr 都会**阻塞**，直到用户按 Esc / 回车取消选中。
#
# 原实现在「⑥ 应用变更 → 重启」的关键路径上有一句 print 提示：
#     用户光标停在控制台里（选中/点了一下） → 该 print 永久阻塞 → _restart_after_response
#     卡死 → 进程不退出 → 退出码 42 发不出去 → start.bat supervisor 永远等不到
#     → 网页表现就是「点了 ⑥ 没反应，只能手动重开 bat」。
#
# 证据：`data/run/restarts.json` 记账在 **22:46:48**，而重启后的新进程启动于 **22:47:44**
#       —— 相隔 56 秒（正常重启链只需约 3 秒）→ 中间确实卡住了。
#
# 三层防御（都不依赖"让用户小心别点控制台"）：
#   ① `_safe_print()`：所有输出改到**守护线程**里执行 —— 即使写操作被冻结，也不会挡住主流程；
#   ② 重启关键路径**只写文件**（`data/run/restart.log`），不写控制台；文件写入不受控制台状态影响；
#   ③ `_forced_exit()` 看门狗：3 秒内无论 shutdown/server_close 出什么问题，都保证按 42 退出。
#
# ⚠️ 第二个坑（2026-09-25 深夜实测，与上面完全独立）：**退出码被吞成 0**
#   `httpd.shutdown()` 一返回，主线程的 `serve_forever()` 立刻返回 → `main()` 走完 →
#   解释器开始收尾 → **守护线程被回收**。而原来的 `os._exit(42)` 恰好跑在一个
#   `daemon=True` 的工作线程里（且中间还要写日志、关套接字，会把 GIL 让出去），
#   于是它常常**还没执行到就被收尾杀掉** → 进程以 0 退出 → start.bat 的
#   `if "%RC%"=="42"` 不成立 → 落到 pause 分支（服务停住、窗口停在"按任意键"）。
#   现象证据：控制台打出 "Server process ended. Exit code = 0"。
#
#   → 修法（四层里的第 ④ 层）：**退出码必须由主线程决定**。
#     工作线程只负责"慢慢关监听"，绝不 `os._exit`；`main()` 在 `serve_forever()` 返回后
#     检查 `RESTART_PENDING`，由主线程 `os._exit(42)` —— 消除竞态，退出码稳定。
#   → 另加第 ⑤ 层兜底：**文件握手** `data/run/relaunch.flag`。进程退出前落下它，
#     start.bat 只看"文件在不在"就决定是否重新拉起，**完全不依赖退出码**。
#
#   教训（方法论）：上一轮的回归脚本是**直接在主线程**调用 `_restart_after_response()`，
#   因此天然拿得到 42、把真 bug 放过去了。现已补 `serve` 模式 —— 用**真实
#   ThreadingHTTPServer + 主线程 serve_forever + 工作线程发起重启**复现生产拓扑。
# 另：`start.bat` 的 `:RESTART` 块同样改为**零控制台输出**（ping 延时 + 静默 goto），
#      否则 cmd 自己的 echo 也会在冻结时卡住接力。
#
# 说明：本方案**不修改**控制台的 QuickEdit 设置（不改注册表、不改窗口属性），
#      因此不影响你鼠标选中/复制控制台文字的能力——只是输出可能被"延迟"到解冻后。

CONSOLE_INFO = {"attached": False, "quick_edit": None, "note": "未探测"}


def _console_probe():
    """只读探测当前控制台的 QuickEdit 状态（供 `/api/code-status` 显示，便于定位冻结）。"""
    if os.name != "nt":
        CONSOLE_INFO["note"] = "非 Windows，跳过"
        return CONSOLE_INFO
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.GetStdHandle(-10)                     # STD_INPUT_HANDLE
        if not h or h == -1:
            CONSOLE_INFO["note"] = "无控制台输入句柄（已重定向），跳过"
            return CONSOLE_INFO
        mode = wintypes.DWORD()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            CONSOLE_INFO["note"] = "非控制台，跳过"
            return CONSOLE_INFO
        CONSOLE_INFO["attached"] = True
        CONSOLE_INFO["quick_edit"] = bool(mode.value & 0x0040)
        CONSOLE_INFO["note"] = ("快速编辑已开启：选中该窗口会冻结其输出（不影响服务，但日志会延迟）"
                                if CONSOLE_INFO["quick_edit"] else "快速编辑已关闭：窗口选中不会冻结输出")
    except Exception as e:  # noqa
        CONSOLE_INFO["note"] = f"探测失败（不影响运行）：{type(e).__name__}: {e}"
    return CONSOLE_INFO


def _safe_print(*parts):
    """输出到控制台，但放到**守护线程**里执行，保证绝不会阻塞调用方。

    正常情况与 print 无异（只是顺序上可能略晚于其它线程的后续输出）；
    控制台被 QuickEdit 冻结时，写操作卡住的只是这个一次性线程，主流程照常。
    """
    text = " ".join(str(p) for p in parts)

    def _w():
        try:
            print(text, flush=True)
        except Exception:
            pass

    try:
        threading.Thread(target=_w, daemon=True).start()
    except Exception:
        pass


def _log_restart_file(msg):
    """重启链路留痕：只写文件，绝不写控制台（控制台可能被冻结）。"""
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(RESTART_LOG, "a", encoding="utf-8") as f:
            f.write("%s pid=%d boot=%s %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), BOOT_ID, msg))
    except Exception:
        pass


def _tail_restart_log(n=6):
    """最近 n 行重启留痕（只读；供 `/api/code-status` 与前端排障展示）。"""
    try:
        with open(RESTART_LOG, "r", encoding="utf-8", errors="replace") as f:
            return [ln.rstrip("\n") for ln in f.readlines()[-n:]]
    except Exception:
        return []


def _write_relaunch_flag(reason):
    """落下「请重新拉起我」的标记文件（supervisor 只看它在不在，**不看退出码**）。"""
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(RELAUNCH_FLAG, "w", encoding="utf-8") as f:
            f.write("%s pid=%d boot=%s reason=%s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), BOOT_ID, reason))
    except Exception:
        pass


def _clear_relaunch_flag():
    """启动时清掉陈旧标记 —— 否则一次异常退出会让下次启动白白多拉一轮。"""
    try:
        if os.path.exists(RELAUNCH_FLAG):
            os.remove(RELAUNCH_FLAG)
    except Exception:
        pass


def _forced_exit(code=RESTART_EXIT_CODE):
    """看门狗：无条件按哨兵码退出 —— 保证 supervisor 不会「永远等不到」。"""
    _log_restart_file("watchdog fired -> forced exit(%d)" % code)
    _write_relaunch_flag("watchdog forced exit(%d)" % code)
    os._exit(code)


def _restart_after_response(httpd, delay=0.6):
    """让 HTTP 响应发完 → 落标记 → 关监听。**本函数绝不 os._exit**（见上方长注释）。

    退出码由主线程在 `serve_forever()` 返回后决定（`_serve_forever_and_exit`）：
    守护线程会在解释器收尾时被回收，从它那里 os._exit 抢不到，退出码会被吞成 0。
    """
    time.sleep(delay)
    _log_restart_file("web UI requested restart -> closing listeners")
    _write_relaunch_flag("web UI requested restart")     # ⑤ 文件握手：先落标记，再关监听
    watchdog = threading.Timer(3.0, _forced_exit)
    watchdog.daemon = False          # 主线程若卡住，这个计时器必须活着把 42 顶出去
    watchdog.start()
    _safe_print(f"[B站历史查看器] 收到网页重启请求 → 释放端口并以退出码 {RESTART_EXIT_CODE} 退出，"
                f"等待 supervisor 拉起…")
    try:
        httpd.shutdown()        # 停止 serve_forever（须在非 serve_forever 线程调用）
        httpd.server_close()    # 释放监听套接字，避免与新实例双绑定
    except Exception as e:  # noqa
        _log_restart_file("shutdown/server_close 异常（忽略，主线程仍按 %d 退出）：%s: %s"
                          % (RESTART_EXIT_CODE, type(e).__name__, e))
    _log_restart_file("listeners closed -> 等主线程决定退出码")


def _begin_restart(httpd, reason="web UI", delay=0.6):
    """统一入口：**同步**置挂起标记（必须先于工作线程，否则主线程可能抢先退出）+ 起工作线程。"""
    RESTART_PENDING["requested"] = True
    RESTART_PENDING["at"] = time.time()
    RESTART_PENDING["reason"] = reason
    threading.Thread(target=_restart_after_response, args=(httpd, delay), daemon=True).start()


def _serve_forever_and_exit(server):
    """主线程跑 serve_forever；返回后**若收到过重启请求，由主线程按哨兵码退出**。

    这是修「退出码被吞成 0」的关键：`os._exit` 必须发生在**非守护线程**里，
    否则解释器收尾会把守护线程连同它将要执行的 os._exit 一起回收掉。
    """
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        RESTART_PENDING["requested"] = False      # Ctrl+C = 用户想停，不当作重启请求
        _safe_print("\n已停止。")
        return
    if RESTART_PENDING["requested"]:
        _log_restart_file("main thread reached exit point -> exit(%d)" % RESTART_EXIT_CODE)
        try:
            server.server_close()
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
    _begin_restart(httpd, "api/restart")
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
        # 控制台冻结自检 + 重启链路留痕（只读；前端排障用）
        "console": dict(CONSOLE_INFO),
        "restart_log": _tail_restart_log(6),
        # 重启请求是否挂起 / 文件握手标记是否已落下（只读；排障用）
        "restart_pending": bool(RESTART_PENDING["requested"]),
        "relaunch_flag": os.path.exists(RELAUNCH_FLAG),
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
        _begin_restart(httpd, "api/apply")
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


# ---------------- 数据源主开关（#21/#1/#2）：mode + Analyzer 库路径 ----------------
# 持久化到 data/source_config.json；优先级：本文件（运行时）> store.py 默认 / 环境变量 ANALYZER_DB
def _source_cfg_path():
    return os.path.join(PROJECT_ROOT, "data", "source_config.json")


def _load_source_config():
    try:
        with open(_source_cfg_path(), "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    m = (d.get("mode") or "auto").strip().lower()
    if m in store.SOURCE_MODES:
        try:
            store.set_source_mode(m)
        except Exception:
            pass
    db = (d.get("analyzer_db") or "").strip()
    if db:
        store.ANALYZER_DB = db
    return d


def _persist_source_config(mode=None, analyzer_db=None):
    cur = {"mode": store.get_source_mode(), "analyzer_db": store.ANALYZER_DB}
    if mode:
        cur["mode"] = mode
    if analyzer_db:
        cur["analyzer_db"] = analyzer_db
    try:
        os.makedirs(os.path.dirname(_source_cfg_path()), exist_ok=True)
        with open(_source_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return cur


_load_source_config()


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


def _forward_fetcher_json(rel_path, body_bytes, timeout=30, method="POST"):
    """转发 **JSON body** 的写请求到 Analyzer（如 /history/update-remark）。

    `_forward_fetcher` 只支持 query params；写类端点需要 body，故单独一条。
    ⚠️ 这是**写操作**：会修改 Analyzer 主库，只在用户显式点击时调用。
    """
    base, key = _fetcher_cfg()
    url = base + rel_path
    try:
        req = urllib.request.Request(url, data=body_bytes, method=method)
        req.add_header("Content-Type", "application/json")
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
                "error": "Analyzer 返回 %s: %s" % (e.code, body[:300])}
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None)
        reason = reason if isinstance(reason, str) else str(e)
        return {"ok": False, "reachable": False,
                "error": "无法连接 Analyzer/Fetcher 后端（%s）：%s" % (base, reason)}


def _fetch_binary(rel_path, timeout=180):
    """取 Analyzer 的**二进制**响应（导出 xlsx / 整库 .db / 本地图片文件）。

    `_forward_fetcher` 只做 text 解码，会破坏 zip/db 字节流，故单独走本条。
    返回 (status, content_type, content_disposition, bytes, error)：error 非空即失败。
    """
    base, key = _fetcher_cfg()
    url = base + rel_path
    try:
        req = urllib.request.Request(url, method="GET")
        if key:
            req.add_header("X-API-Key", key)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (r.status, r.headers.get("Content-Type"),
                    r.headers.get("Content-Disposition"), r.read(), None)
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return (e.code, None, None, body,
                "Analyzer 返回 %s：%s" % (e.code, body[:200].decode("utf-8", "replace")))
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None)
        reason = reason if isinstance(reason, str) else str(e)
        return (0, None, None, b"", "无法连接 Analyzer/Fetcher 后端（%s）：%s" % (base, reason))


def _sessdata_status():
    """凭证健康（#27）：只读探测 Analyzer 的 `/login/check`。

    该端点用 **Analyzer `config/config.yaml` 里的 SESSDATA** 去调 B站 nav 接口，
    因此它反映的是「主源凭证」的存活状态，与 Finder `config.json` 那份无关（且不需要）。
    **纯只读**：只发 GET，不改任何数据、不触发拉取。

    状态：ok（code=0 且 isLogin）/ invalid（-101 未登录）/ unknown（不可达或异常）
    """
    r = _forward_fetcher("/login/check", timeout=8)
    if not r.get("reachable"):
        return {"state": "unknown", "reason": "Analyzer 不可达", "detail": r.get("error"),
                "owner": "Analyzer(config.yaml)"}
    d = r.get("data") if isinstance(r.get("data"), dict) else {}
    code = d.get("code")
    payload = d.get("data") if isinstance(d.get("data"), dict) else {}
    if code == 0 and payload.get("isLogin"):
        return {"state": "ok", "code": 0, "uname": payload.get("uname"),
                "vip": payload.get("vipStatus") == 1,
                "owner": "Analyzer(config.yaml)"}
    if code == -101:
        return {"state": "invalid", "code": code,
                "message": d.get("message") or "未登录",
                "owner": "Analyzer(config.yaml)",
                "hint": "请更新 Analyzer 的 config/config.yaml 中的 SESSDATA（Analyzer 自带邮件告警，已在跑）"}
    return {"state": "unknown", "code": code, "message": d.get("message"),
            "owner": "Analyzer(config.yaml)"}


def _health_payload(with_sessdata=True):
    """`/api/fetcher-health` 的响应体：可达性 + （可选）凭证健康 + 数据源实际生效模式。"""
    res = _forward_fetcher("/health", timeout=5)
    res = dict(res) if isinstance(res, dict) else {"ok": False, "reachable": False}
    if with_sessdata and res.get("reachable"):
        res["sessdata"] = _sessdata_status()
    elif with_sessdata:
        res["sessdata"] = {"state": "unknown", "reason": "Analyzer 不可达",
                           "owner": "Analyzer(config.yaml)"}
    try:
        res["source"] = store.source_status()
    except Exception as e:  # noqa
        res["source"] = {"error": str(e)}
    return res


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


def _after_data_pull(payload):
    """拉取成功后：① 刷新内存缓存（否则新数据不会显示）；② 按 #26 策略评估自动备份。

    只做「重读 + 条件备份」，**不写任何历史数据**。
    """
    out = {"reloaded": False, "auto_backup": None}
    try:
        reload_all()
        out["reloaded"] = True
    except Exception as e:  # noqa
        out["reload_error"] = str(e)
    try:
        out["auto_backup"] = _maybe_auto_backup(reason="拉取后新增达到阈值")
    except Exception as e:  # noqa
        out["auto_backup"] = {"triggered": False, "reason": str(e)}
    return out


def _backup_policy_path():
    return os.path.join(PROJECT_ROOT, "data", "backup_policy.json")


def _load_backup_policy():
    """#26 备份策略（用户选定「走 b」的变体：**按新增视频条数触发**，而非定时）。

    - auto            ：是否启用自动触发（默认 true）
    - delta_threshold ：自上次备份以来 Analyzer 主源**新增条数**达到该值 → 自动备份一次
    - keep            ：只保留最近 N 份快照，超出自动清理（防 data/backup/ 无限累积）
    """
    d = {"auto": True, "delta_threshold": 200, "keep": 5}
    try:
        with open(_backup_policy_path(), "r", encoding="utf-8") as f:
            d.update(json.load(f) or {})
    except Exception:
        pass
    return d


def _prune_backups(keep):
    """只保留最近 keep 份快照（目录名形如 YYYYmmdd-HHMMSS，可字符串倒序）。返回被删列表。"""
    keep = max(0, int(keep or 0))
    if not os.path.isdir(BACKUP_ROOT):
        return []
    names = sorted([n for n in os.listdir(BACKUP_ROOT)
                    if os.path.isdir(os.path.join(BACKUP_ROOT, n))], reverse=True)
    removed = []
    for n in names[keep:]:
        try:
            shutil.rmtree(os.path.join(BACKUP_ROOT, n))
            removed.append(n)
        except Exception:
            pass
    return removed


def _backup_policy_status():
    """只读：回报策略、上次备份条数、当前条数、增量与"是否达到触发线"。**不触发备份。**"""
    pol = _load_backup_policy()
    backups = _list_backups()
    latest = backups[0] if backups else None
    last_records = None
    if latest:
        for it in latest.get("items", []):
            if it.get("label") == "analyzer":
                last_records = it.get("records")
    cur = store.source_status().get("analyzer") or 0
    delta = None if last_records is None else int(cur) - int(last_records)
    thr = int(pol.get("delta_threshold") or 0)
    return {
        "policy": pol,
        "backup_count": len(backups),
        "latest_at": latest.get("created_at") if latest else None,
        "latest_analyzer_records": last_records,
        "current_analyzer_records": cur,
        "delta_since_last_backup": delta,
        "would_trigger": bool(pol.get("auto")) and delta is not None and delta >= thr,
        "pending_prune": max(0, len(backups) - int(pol.get("keep") or 0)),
    }


def _maybe_auto_backup(reason=""):
    """#26：**仅在数据刚增长后**被调用。条件满足则备份一次并清理旧快照。

    触发条件：delta = 当前 Analyzer 主源条数 − 上次备份记录条数 ≥ delta_threshold。
    首次（还没有任何备份）不触发——避免刚启动就凭空写盘；由用户手点 ⑤ 建立基线。
    """
    pol = _load_backup_policy()
    if not pol.get("auto"):
        return {"triggered": False, "reason": "auto=false"}
    st = _backup_policy_status()
    if st["latest_analyzer_records"] is None:
        return {"triggered": False, "reason": "尚无备份基线（请先手点 ⑤ 建立）"}
    if not st["would_trigger"]:
        return {"triggered": False, "reason": "增量未达阈值", "delta": st["delta_since_last_backup"],
                "threshold": pol.get("delta_threshold")}
    try:
        manifest = _create_backup()
        removed = _prune_backups(pol.get("keep"))
        return {"triggered": True, "reason": reason, "delta": st["delta_since_last_backup"],
                "manifest": manifest, "pruned": removed}
    except Exception as e:  # noqa
        return {"triggered": False, "reason": "备份失败：%s" % e}


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

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
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
            # #27：同时附带凭证健康（?sessdata=0 可跳过）与数据源实际生效模式
            self._send(200, _health_payload(with_sessdata=flat.get("sessdata", "1") != "0"))
            return
        if path == "/api/data-source":
            # #21 数据源主开关：读取当前模式 + 实际生效数据源（纯读）
            self._send(200, {
                "ok": True,
                "mode": store.get_source_mode(),
                "modes": list(store.SOURCE_MODES),
                "status": store.source_status(),
            })
            return
        if path == "/api/fetcher-trigger":
            # 触发 Analyzer 重新拉取/分析：?mode=full 走全量，默认增量
            mode = flat.get("mode") or ""
            # sync_deleted：默认同步删除记录（保持与 B 站一致）；前端可传 0 跳过
            sync_deleted = flat.get("sync_deleted", "1")
            params = {"sync_deleted": sync_deleted}
            if mode == "full":
                res = _forward_fetcher("/fetch/bili-history", timeout=240, params=params)
                if isinstance(res, dict) and res.get("ok"):
                    res = dict(res)
                    res["post"] = _after_data_pull(res)
                self._send(200, res)
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
                    "post": _after_data_pull(full) if full.get("ok") else None,
                })
                return
            if isinstance(inc, dict) and inc.get("ok"):
                inc = dict(inc)
                inc["post"] = _after_data_pull(inc)
            self._send(200, inc)
            return
        if path == "/api/fetcher-check":
            # Step④ 数据自检（重新实现）：真正调用 Analyzer 完整性校验 + 报告
            self._send(200, _analyzer_integrity_check())
            return
        if path == "/api/backups":
            self._send(200, {"backups": _list_backups()})
            return
        if path == "/api/backup-policy":
            # #26 备份触发/保留策略（纯读：只回报计数与阈值，不触发备份）
            self._send(200, {"ok": True, "policy": _backup_policy_status()})
            return
        # ---- #25 导出中继（对齐 Frontend：自己不生成文件，全部转发 Analyzer /export/*）----
        if path == "/api/export/db":
            st, ct, cd, blob, err = _fetch_binary("/export/download_db")
            if err:
                self._send(502, {"ok": False, "error": err})
                return
            self._send(st or 200, blob, ct or "application/octet-stream",
                       {"Content-Disposition": cd} if cd else None)
            return
        if path.startswith("/api/export/excel/"):
            fn = urllib.parse.unquote(path[len("/api/export/excel/"):])
            st, ct, cd, blob, err = _fetch_binary(
                "/export/download_excel/" + urllib.parse.quote(fn))
            if err:
                self._send(502, {"ok": False, "error": err})
                return
            self._send(st or 200, blob, ct or "application/octet-stream",
                       {"Content-Disposition": cd} if cd else None)
            return
        # ---- #23 图片批量下载中继（对齐 Frontend /images/*）----
        if path == "/api/images/status":
            self._send(200, _forward_fetcher("/images/status", timeout=10))
            return
        if path.startswith("/api/images/local/"):
            tail = path[len("/api/images/local/"):]
            st, ct, cd, blob, err = _fetch_binary("/images/local/" + tail, timeout=60)
            if err:
                self._send(404, {"ok": False, "error": err})
                return
            self._send(st or 200, blob, ct or "application/octet-stream")
            return
        if path == "/api/images/start-params":
            # 供前端展示默认冒烟参数（不触发下载）：/images/start 必需参数说明
            self._send(200, {"ok": True, "params": {"year": None, "use_sessdata": False},
                             "hint": "POST /api/images/start?year=2026&use_sessdata=false 为安全冒烟组合"})
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

        # ---- #25 导出中继：生成 Excel（转发 Analyzer，不自己写 xlsx）----
        if parsed.path == "/api/export/excel":
            q = {k: v[0] for k, v in qs.items() if k in ("year", "month", "start_date", "end_date")}
            r = _forward_fetcher("/export/export_history", timeout=180, method="POST", params=q)
            self._send(200 if r.get("ok") else 502, r)
            return

        # ---- #24 remark 编辑中继（写 Analyzer 主库；Finder 不落第三份数据）----
        if parsed.path == "/api/remark":
            bvid = (body.get("bvid") or "").strip()
            view_at = body.get("view_at")
            remark = body.get("remark")
            if not bvid or view_at in (None, ""):
                self._send(400, {"ok": False, "error": "需要 bvid 与 view_at"})
                return
            try:
                view_at = int(view_at)
            except Exception:
                self._send(400, {"ok": False, "error": "view_at 必须是整数时间戳"})
                return
            payload = json.dumps({"bvid": bvid, "view_at": view_at,
                                  "remark": "" if remark is None else str(remark)}).encode("utf-8")
            r = _forward_fetcher_json("/history/update-remark", payload, timeout=30)
            self._send(200 if r.get("ok") else 502, r)
            return

        # ---- #23 图片批量下载中继（start/stop/clear 会写盘，故前端默认走 use_sessdata=false 冒烟）----
        if parsed.path in ("/api/images/start", "/api/images/stop", "/api/images/clear"):
            action = parsed.path.rsplit("/", 1)[-1]
            q = {k: v[0] for k, v in qs.items() if k in ("year", "use_sessdata")}
            r = _forward_fetcher("/images/" + action, timeout=30, method="POST", params=q)
            self._send(200 if r.get("ok") else 502, r)
            return

        if parsed.path == "/api/data-source":
            # #21 数据源主开关：切换模式（可选改 Analyzer 库路径）→ 持久化 + 立即 reload（不重启）
            mode = (body.get("mode") or "").strip().lower()
            db = (body.get("analyzer_db") or "").strip()
            if mode and mode not in store.SOURCE_MODES:
                self._send(400, {"ok": False,
                                 "error": "mode 必须是 %s 之一" % (list(store.SOURCE_MODES),)})
                return
            if db:
                store.ANALYZER_DB = db
            if mode:
                store.set_source_mode(mode)
            _persist_source_config(mode=mode or None, analyzer_db=db or None)
            try:
                reload_all()
            except Exception as e:  # noqa
                self._send(200, {"ok": False, "error": "切换后重载失败：%s" % e,
                                 "mode": store.get_source_mode(),
                                 "status": store.source_status()})
                return
            self._send(200, {"ok": True, "mode": store.get_source_mode(),
                             "status": store.source_status(),
                             "banner": store.get_meta_banner(store.get_raw())})
            return

        if parsed.path == "/api/sync":
            full = qs.get("full", ["0"])[0] == "1"
            mode = store.get_source_mode()
            if mode in ("auto", "analyzer"):
                # #1/#2「计划 A」：凭证单一化 —— 不再跑本地 collector（其 config.json 的 SESSDATA 已失效/弃用），
                # 改为**触发 Analyzer 全量**：抓取执行者与凭证都归 Analyzer（即"中继"，非直连）。
                # 与 /api/fetcher-trigger?mode=full 同语义，保留 /api/sync 这个既有按钮入口不破坏前端。
                res = _forward_fetcher("/fetch/bili-history", timeout=240, method="GET",
                                       params={"sync_deleted": qs.get("sync_deleted", ["1"])[0]})
                self._send(200, {
                    "started": bool(res.get("ok")),
                    "engine": "analyzer",
                    "mode": mode,
                    "note": "计划A：走 Analyzer 全量拉取（不再使用 Finder 本地 collector 凭证）",
                    "result": res,
                    "post": _after_data_pull(res) if res.get("ok") else None,
                })
                return
            # mode == local：模式 B（自身抓取），保留原 collector 语义，需自备有效 SESSDATA
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
    _console_probe()            # 只读探测 QuickEdit（影响"输出是否可能被冻结"，不影响功能）
    port = get_web_port()
    ensure_rules()
    reload_all()
    # 首次启动自动校准一次：若从未应用过规则，按默认规则跑一遍（dry 仅标记应用时间，不写源库）
    if not store.get_meta("rule_applied_hash"):
        try:
            _store_applied_rules()
            _safe_print("[B站历史查看器] 首次启动：已按当前规则固化『已应用』状态")
        except Exception as e:  # noqa
            _safe_print(f"[B站历史查看器] 首次校准失败：{e}")
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
            _safe_print(f"[B站历史查看器] 端口 {port} 已被占用 —— 服务很可能已经在运行。\n"
                        f"                 直接打开 http://127.0.0.1:{port} 即可；\n"
                        f"                 若想重启，请在网页上点「⑥ 应用变更」，或先跑 stop.bat。")
        else:
            _safe_print(f"[B站历史查看器] 无法绑定端口 {port}：{e}")
        time.sleep(0.25)        # 给守护线程一点时间把上面这句写出去（该路径随即退出）
        sys.exit(1)
    banner = store.get_meta_banner(store.get_raw())
    lines = [
        f"[B站历史查看器] 实例: boot_id={BOOT_ID}  pid={os.getpid()}  code={_code_version()}  "
        f"supervised={'yes' if _is_supervised() else 'no'}",
        f"[B站历史查看器] 已启动: http://127.0.0.1:{port}  (也可访问 http://localhost:{port})",
        f"[B站历史查看器] 数据源: Analyzer(主,只读)={banner['analyzer']} 条 + "
        f"本地Finder(备份,只读)={banner['local_backup']} 条 → 合并 {banner['total']} 条",
        f"[B站历史查看器] Analyzer 库: {banner['analyzer_db']}",
    ]
    try:
        st = rules_status()
        lines.append(f"[B站历史查看器] 规则状态: 待应用(pending)={st.get('pending')}  "
                     f"自上次应用以来新增未套用(dirty_count)={st.get('dirty_count')}")
    except Exception:
        pass
    lines.append(f"[B站历史查看器] 控制台: {CONSOLE_INFO.get('note')}")
    lines.append("[B站历史查看器] 按 Ctrl+C 停止（关闭本窗口也会停止服务）")
    _log_restart_file("boot ok -> listen on %s:%d" % (bind_host, port))
    _clear_relaunch_flag()          # 本次已成功启动 → 陈旧的重拉标记作废
    _safe_print("\n".join(lines))
    _serve_forever_and_exit(server)  # 退出码由主线程决定（见该函数说明）


if __name__ == "__main__":
    main()
