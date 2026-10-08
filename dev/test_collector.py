# -*- coding: utf-8 -*-
"""`collector.py` **离线部分**单测 —— B站采集器里**不发实网请求**的那一半。

为什么只测一半：`collector.py` 643 行里，`fetch_page` / `_fetch_loop` / `run` /
`main` **都要真发 B站请求**（属需授权的 B 组，见 `dev/README` §6.5）。但下面这些
**零 IO / 纯函数**，可以在沙箱里完整覆盖：

  · `classify_api_code`   B站 code → (kind, message)
  · `classify_http_status` HTTP 状态 → (kind, message)
  · `extract_fields`      原始 JSON item → 落库字段（含 `kid` 兜底构造）
  · `init_db` / `upsert` / `delete_records`   本地库读写（**临时库**）
  · `_load_cursor` / `_save_cursor`          断点续拉游标持久化

⚠️ 本文件**只在 `tempfile.mkdtemp()` 建的临时库里写**，绝不碰 `data/`。
"""
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import collector  # noqa: E402

_OK = 0
_FAIL = 0
_FAILED = []


def check(cond, tag, detail=""):
    global _OK, _FAIL
    if cond:
        _OK += 1
    else:
        _FAIL += 1
        _FAILED.append("%s%s" % (tag, ("  | " + str(detail)) if detail else ""))
        print("  x FAIL %s%s" % (tag, ("  | " + str(detail)) if detail else ""))


def eq(got, want, tag):
    check(got == want, tag, "got=%r want=%r" % (got, want))


def sec(t):
    print("\n-- %s" % t)


# ---------------------------------------------------------------- 1. 错误码分类

def t1_codes():
    sec("C1 `classify_api_code` / `classify_http_status` —— 错误分类（纯函数）")
    # 语义分三类：**fatal**（重试无用：登录态/参数）· **backoff**（风控/限流：退避后再试）
    #   · **retry**（未收录：保守多重试几次）
    for code in (-101, -111, 11000, -400):
        eq(collector.classify_api_code(code)[0], "fatal",
           "code %d → fatal（重试无用）" % code)
    for code in (-799, -509, -412, -403):
        kind, msg = collector.classify_api_code(code)
        eq(kind, "backoff", "code %d → backoff（风控/限流，退避后重试）" % code)
        check(bool(msg), "code %d 有可读原因" % code)

    eq(collector.classify_api_code(999999)[0], "retry", "未收录 code → retry（保守）")
    eq(collector.classify_api_code(None)[0], "retry", "None → retry（不崩）")

    # ⚠️ 照实：**HTTP 4xx 只有 403/412/429 被收录（→ backoff）**，其余（400/401 等）落 retry。
    #   这不是 bug 而是「B站实际不会返回 400/401 给采集端」的现状，但**未收录即 retry**
    #   意味着若真返回 400 会白重试 3 次。
    eq(collector.classify_http_status(403)[0], "backoff", "HTTP 403 → backoff（疑被风控）")
    eq(collector.classify_http_status(412)[0], "backoff", "HTTP 412 → backoff（风控）")
    eq(collector.classify_http_status(429)[0], "backoff", "HTTP 429 → backoff（限流）")
    eq(collector.classify_http_status(400)[0], "retry", "【照实】HTTP 400 未收录 → retry")
    eq(collector.classify_http_status(401)[0], "retry", "【照实】HTTP 401 未收录 → retry")
    eq(collector.classify_http_status(500)[0], "retry", "HTTP 500 → retry（服务端错误可重试）")
    eq(collector.classify_http_status(None)[0], "retry", "None → retry（不崩）")

    # ⚠️ **已知缺口**：`#13` 记过「风控码只处理 -101/-111，未见 -352/-412」。
    #   实测（2026-10-05）**该记录已过时**：`-412` 现已收录为 `backoff`；
    #   **只有 `-352` 仍未收录** → 会落 retry（可能对风控码硬重试）。
    eq(collector.classify_api_code(-412)[0], "backoff",
       "【已收录】风控码 -412 → backoff（`#13` 旧记录「未处理 -412」已过时）")
    eq(collector.classify_api_code(-352)[0], "retry",
       "【已知缺口·照实】风控码 -352 **仍未收录** → retry（可能硬重试）")


# ---------------------------------------------------------------- 2. 字段提取

def t2_extract():
    sec("C2 `extract_fields` —— 原始 JSON → 落库字段")
    # 完整一条（仿 Analyzer 真实 JSON 结构：bvid 在 history 子对象里）
    item = {
        "title": "测试标题", "author_name": "作者", "author_mid": 12345,
        "view_at": 1700000000, "duration": 300, "progress": 30,
        "business": "archive", "cover": "http://x/c.jpg", "kid": "K1",
        "history": {"bvid": "BV1xx", "oid": 999, "cid": 888, "epid": 0, "business": "archive"},
    }
    f = collector.extract_fields(item)
    eq(f["kid"], "K1", "有 kid 时直接用")
    eq(f["bvid"], "BV1xx", "**bvid 从 history 子对象取**（顶层没有）")
    eq(f["author_mid"], "12345", "author_mid 转字符串")
    eq(f["duration"], 300, "duration 透传")

    # kid 缺失 → 兜底构造
    f2 = collector.extract_fields({**item, "kid": None, "view_at": 1700000000})
    check(f2["kid"] and "_" in f2["kid"], "kid 缺失 → 用 bvid_viewAt 兜底", "got=%r" % f2["kid"])

    # 全空：不得崩
    f3 = collector.extract_fields({})
    check(isinstance(f3, dict) and f3.get("kid") is not None,
          "空 item → 不崩且 kid 可用", "got=%r" % f3.get("kid"))

    # covers 兜底 cover
    f4 = collector.extract_fields({**item, "cover": None, "covers": ["http://y/1.jpg"]})
    eq(f4.get("cover"), "http://y/1.jpg", "cover 缺失 → 取 covers[0]")


# ---------------------------------------------------------------- 3. 本地库读写

def t3_db():
    sec("C3 `init_db` / `upsert` / `delete_records` —— 临时库（**不碰 data/**）")
    tmp = tempfile.mkdtemp(prefix="bhf_col_")
    db = os.path.join(tmp, "t.db")
    try:
        collector.init_db(db)
        check(os.path.exists(db), "init_db 建库", "path=%s" % db)
        con = sqlite3.connect(db)
        cols = [r[1] for r in con.execute("PRAGMA table_info(history)")]
        for c in ("kid", "view_at", "progress", "duration", "auto_skip"):
            check(c in cols, "表含列 %s" % c, "实际=%s" % cols[:12])

        base = collector.extract_fields({
            "title": "A", "author_name": "u", "author_mid": 1, "view_at": 1700000000,
            "duration": 100, "progress": 10, "business": "archive", "kid": "K1",
            "history": {"bvid": "BV1", "oid": 1, "cid": 1, "epid": 0, "business": "archive"},
        })
        now = int(__import__("time").time())
        collector.upsert(con, base, now)
        collector.upsert(con, dict(base, title="A2", progress=50), now)
        n = con.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        eq(n, 1, "**同 kid 重复 upsert → 仍 1 条**（不重复插入）")
        got = con.execute("SELECT title, progress FROM history WHERE kid='K1'").fetchone()
        eq(got[0], "A2", "重复 upsert 覆盖 title（取新值）")
        eq(got[1], 50, "重复 upsert 覆盖 progress")

        # 游标
        eq(collector._load_cursor(con), None, "初始无游标 → None")
        collector._save_cursor(con, (12345, 1700000000, "archive"))
        cur = collector._load_cursor(con)
        eq(cur, (12345, 1700000000, "archive"), "游标存取往返一致")
        # 坏游标
        con.execute("UPDATE meta SET value='{坏 json' WHERE key='sync_cursor'")
        eq(collector._load_cursor(con), None, "**坏游标 → None**（不抛）")

        # ⚠️ 必须先 **commit 并关连接** 再调 delete_records ——
        #   `delete_records(db_path, kids)` 自己 `init_db()` 开**新连接**（实测 collector.py L355），
        #   若本连接还持有未提交写 → `sqlite3.OperationalError: database is locked`。
        #   （这是**测试写法**要注意的点，不是 collector 的缺陷 —— 生产里调用方不会同时持有写事务。）
        con.commit()
        n0 = con.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        collector.delete_records(db, ["K1"])
        n1 = con.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        eq(n1, n0 - 1, "delete_records 删掉指定 kid")
        collector.delete_records(db, [])
        eq(con.execute("SELECT COUNT(*) FROM history").fetchone()[0], n1,
           "空列表删除 → 不动数据（不崩）")
        collector.delete_records(db, ["不存在的kid"])
        eq(con.execute("SELECT COUNT(*) FROM history").fetchone()[0], n1,
           "删不存在的 kid → 不崩、条数不变")
        # 库文件不存在 → 返回 0 且**不建库**（A8 契约）
        missing = os.path.join(tmp, "不存在的.db")
        eq(collector.delete_records(missing, ["K1"]), 0, "库不存在 → 返回 0")
        check(not os.path.exists(missing), "**库不存在时不建库**（A8：避免凭空造空库）")
        con.close()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        print("  （临时库已清理：%s）" % tmp)


# ---------------------------------------------------------------- main

def main():
    for fn in (t1_codes, t2_extract, t3_db):
        fn()
    print("\n" + "=" * 60)
    if _FAIL:
        print("  结果：%d 通过 / %d 失败" % (_OK, _FAIL))
        for f in _FAILED:
            print("    x %s" % f)
        return 1
    print("  结果：全部通过（%d 项断言）" % _OK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
