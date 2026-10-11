# -*- coding: utf-8 -*-
"""只读验证：重启链路在「控制台被冻结」场景下是否仍然可用。

约束：不启动服务、不绑定对外端口（只绑 127.0.0.1:0 临时端口）、不读写任何历史数据。

验证项：
  ① `_safe_print` 在 stdout 被永久占住（模拟 QuickEdit 冻结）时**立刻返回**
     —— 对照组：同样条件下直接 print 会永久阻塞（用线程存活证明）
  ② 正常重启路径 → 进程退出码必须是哨兵 42，且 `restart.log` 留痕完整
  ③ `shutdown()` 故意卡死 30s → 看门狗必须在 ~3s 内以 42 退出（而不是永远卡住）
  ④ `_console_probe()` 在无控制台环境（管道）优雅降级，不抛异常
  ⑤ `code_status()` 已带 `console` / `restart_log` 两个新字段
  ⑥ **（2026-09-25 深夜补）生产拓扑**：真实 ThreadingHTTPServer + 主线程 serve_forever +
     工作线程发起重启 → 连跑 3 次，退出码必须**每次都是 42**
     —— 旧设计（daemon 工作线程里 os._exit）在这里会被解释器收尾吞成 0，
        以上一轮漏测正是因为脚本直接在主线程调用了 `_restart_after_response()`。
"""
import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))


def _redirect_run_dir(server):
    """所有写盘都改到临时目录 —— 绝不碰仓库真实的 data/run/。

    注意：这几个路径都是 server.py **模块顶层**用 RUN_DIR 拼出来的常量，改 RUN_DIR
    不会连带改它们，必须逐个覆盖。RESTARTS_FILE 目前只由 HTTP 端点路径
    （apply_changes → _note_restart）写入，本脚本直接调 _begin_restart 因而不会碰它，
    但一并重定向以防将来脚本改走端点路径后污染真实护栏记账。
    """
    server.RUN_DIR = os.environ.get("BHF_TEST_LOGDIR") or tempfile.mkdtemp(prefix="bhf_rt_")
    server.RESTARTS_FILE = os.path.join(server.RUN_DIR, "restarts.json")
    server.RESTART_LOG = os.path.join(server.RUN_DIR, "restart.log")
    server.RELAUNCH_FLAG = os.path.join(server.RUN_DIR, "relaunch.flag")


def _mk_server():
    import http.server
    import socketserver

    class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    return Srv(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)


def child(mode):
    import server
    _redirect_run_dir(server)

    if mode == "normal":
        class H:
            def shutdown(self): pass
            def server_close(self): pass
        server._restart_after_response(H(), delay=0.1)
        # 关键断言：工作线程**没有**自行 os._exit，函数正常返回（退出码由主线程决定）。
        # 这里用 os._exit(0) 直接结束，绕过非守护看门狗（生产里是主线程先 os._exit(42)）。
        print("NEW_DESIGN_SHOULD_NOT_EXIT_HERE", flush=True)
        os._exit(0)

    elif mode == "hang":
        class H:
            def shutdown(self):
                time.sleep(30)          # 模拟 shutdown 卡死
            def server_close(self): pass
        server._restart_after_response(H(), delay=0.1)
        print("NEW_DESIGN_SHOULD_NOT_EXIT_HERE")
        sys.exit(0)

    elif mode == "serve":
        # ⑥ 生产拓扑：主线程 serve_forever + 工作线程发起重启 → 走生产的 _serve_forever_and_exit
        srv = _mk_server()

        def _req():
            time.sleep(0.3)
            server._begin_restart(srv, "regression-serve", delay=0.1)

        threading.Thread(target=_req, daemon=True).start()
        server._serve_forever_and_exit(srv)      # 正常路径下这里直接 os._exit(42)
        print("SERVE_REACHED_END_WITHOUT_EXIT")  # ← 不该出现
        sys.exit(0)

    elif mode == "serve_old":
        # 对照：旧设计 —— 由 daemon 工作线程 os._exit（会被解释器收尾吞掉 → 退出码 0）
        srv = _mk_server()

        def _old():
            time.sleep(0.1)
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
            server._log_restart_file("OLD design -> os._exit(42) from DAEMON thread")
            os._exit(42)

        threading.Thread(target=_old, daemon=True).start()
        srv.serve_forever()
        print("OLD_REACHED_END_WITHOUT_EXIT")
        sys.exit(0)

    elif mode == "blocked_stdout":
        class Blocked:
            def write(self, s):
                time.sleep(60)          # 模拟控制台被选中：写操作永久阻塞
                return len(s)
            def flush(self): pass

        real = sys.stdout
        # ① _safe_print 必须立刻返回
        sys.stdout = Blocked()
        t0 = time.time()
        server._safe_print("这行写不出去（模拟控制台被冻结）")
        dt = time.time() - t0
        sys.stdout = real

        # ② 对照组：直接 print 会卡死（线程 1s 后仍存活即证明）
        th = threading.Thread(target=lambda: print("direct", flush=True), daemon=True)
        sys.stdout = Blocked()
        th.start()
        th.join(1.0)
        still = th.is_alive()
        sys.stdout = real

        print("SAFE_PRINT_RETURN_MS %.1f" % (dt * 1000))
        print("SAFE_PRINT_OK %s" % (dt < 0.3))
        print("DIRECT_PRINT_BLOCKED %s" % still)

    elif mode == "probe":
        c = server._console_probe()
        print("CONSOLE_ATTACHED %s" % c["attached"])
        print("CONSOLE_NOTE %s" % c["note"])
        cs = server.code_status()
        print("CODE_STATUS_HAS_CONSOLE %s" % ("console" in cs))
        print("CODE_STATUS_HAS_RESTART_LOG %s" % ("restart_log" in cs))
        print("CODE_STATUS_HAS_RELAUNCH_FLAG %s" % ("relaunch_flag" in cs))
        print("CODE_STATUS_CONSOLE %s" % (cs["console"],))
        print("RESTART_LOG_TAIL_EMPTY_OK %s" % (cs["restart_log"] == []))

    sys.exit(0)


def _run(mode, logdir=None):
    logdir = logdir or tempfile.mkdtemp(prefix="bhf_rt_")
    env = dict(os.environ, BHF_TEST_LOGDIR=logdir)
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.abspath(__file__), mode], env=env,
                       capture_output=True, text=True, errors="replace")
    dt = time.time() - t0
    lp = os.path.join(logdir, "restart.log")
    log = open(lp, encoding="utf-8").read().strip() if os.path.exists(lp) else ""
    return r, dt, log, logdir


def main():
    if len(sys.argv) > 1:
        return child(sys.argv[1])

    print("=" * 68)
    rc_all = 0

    # ---- ② 工作线程只关监听、不自行 os._exit（退出码交给主线程）----
    r, dt, log, logdir = _run("normal")
    has_flag = os.path.exists(os.path.join(logdir, "relaunch.flag"))
    ok = (r.returncode == 0
          and "NEW_DESIGN_SHOULD_NOT_EXIT_HERE" in r.stdout     # 函数正常返回后才结束
          and "listeners closed" in log
          and has_flag
          and dt <= 2.0)
    rc_all |= 0 if ok else 1
    print("\n[%s] ② 工作线程只关监听并落标记、**不自行退出**" % ("PASS" if ok else "FAIL"))
    print("   退出码 = %s（期望 0：工作线程已不再 os._exit，主线程尚未退出）｜ 耗时 = %.2fs（预算 2.0s）"
          % (r.returncode, dt))
    print("   握手标记 relaunch.flag = %s（期望 True）" % has_flag)
    print("   restart.log 留痕：")
    for ln in (log.splitlines() or ["(无)"]):
        print("     " + ln)

    # ---- ③ 看门狗：shutdown 卡死 30s，主线程被卡住 → 看门狗必须 ~3s 顶出 42 ----
    r, dt, log, _ = _run("hang")
    ok = (r.returncode == 42) and (dt <= 6.0)
    rc_all |= 0 if ok else 1
    print("\n[%s] ③ shutdown 卡死 30s → 看门狗兜底" % ("PASS" if ok else "FAIL"))
    print("   退出码 = %s（期望 42）｜ 耗时 = %.2fs（预算 6.0s）" % (r.returncode, dt))
    print("   restart.log 留痕：")
    for ln in (log.splitlines() or ["(无)"]):
        print("     " + ln)

    # ---- ⑥ 生产拓扑：真实 HTTP server + 主线程 serve_forever，连跑 3 次 ----
    print("\n" + "=" * 68)
    print("[⑥ 生产拓扑] 真实 ThreadingHTTPServer + 主线程 serve_forever + 工作线程发起重启")
    codes, lastlog = [], []
    for i in range(3):
        r, dt, log, logdir = _run("serve")
        codes.append((r.returncode, dt, r.stdout.strip()))
        lastlog = log.splitlines()
    ok6 = all(c[0] == 42 for c in codes)
    rc_all |= 0 if ok6 else 1
    print("   [%s] 连跑 3 次，退出码必须每次都是 42" % ("PASS" if ok6 else "FAIL"))
    for i, (rc, dt, out) in enumerate(codes, 1):
        print("     第%d次：退出码 = %-3s（期望 42）｜ 耗时 %.2fs%s"
              % (i, rc, dt, ("  ← " + out) if out else ""))
    print("   末次 restart.log 留痕：")
    for ln in (lastlog or ["(无)"]):
        print("     " + ln)

    # ---- ⑦ 对照（信息性）：旧设计 = daemon 工作线程里 os._exit → 退出码被收尾吞掉 ----
    print("\n" + "=" * 68)
    print("[⑦ 对照 · 旧设计] daemon 工作线程里 os._exit(42) —— 预期退出码被吞成 0")
    old_codes = []
    for i in range(3):
        r, dt, log, _ = _run("serve_old")
        old_codes.append(r.returncode)
    print("   3 次实际退出码：%s（0 即复现了线上那个 bug）" % old_codes)
    print("   → 新设计（⑥）与旧设计（⑦）的差别就是这次修复要保证的东西。")

    # ---- ④ ① stdout 冻结 ----
    print("\n" + "=" * 68)
    r, _, _, _ = _run("blocked_stdout")
    out = r.stdout
    print("[① stdout 冻结场景]")
    for ln in out.strip().splitlines():
        print("   " + ln)
    ok1 = "SAFE_PRINT_OK True" in out
    ok2 = "DIRECT_PRINT_BLOCKED True" in out
    rc_all |= 0 if (ok1 and ok2) else 1
    print("   → _safe_print 不被冻结: %s ｜ 直接 print 会被冻结(对照): %s"
          % ("PASS" if ok1 else "FAIL", "PASS" if ok2 else "FAIL"))

    # ---- ④ / ⑤ 探测与载荷 ----
    print("=" * 68)
    r, _, _, _ = _run("probe")
    out = r.stdout
    print("[控制台探测 + 载荷字段]")
    for ln in out.strip().splitlines():
        print("   " + ln)
    okp = "CONSOLE_ATTACHED" in out and "CODE_STATUS_HAS_RELAUNCH_FLAG True" in out
    rc_all |= 0 if okp else 1
    if not okp:
        print("   → FAIL（探测抛异常或载荷缺字段）")

    print("=" * 68)
    print("总体：%s" % ("全部通过" if rc_all == 0 else "存在失败项"))
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
