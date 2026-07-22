#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站历史记录采集器 — 同步核心（全量基线 + 增量续拉，最终策略见原型方案 §12）

同步策略（§12 最终可执行版）：
- 全量同步 = 仅初始建基线（一次性，无基线无法增量续拉）。
- 之后默认只做增量同步（--incremental）：拉到 view_at < 上次同步时刻即停。
- 完成判定：以结论文件 sync_result.json 的 `completed` 为准（cursor.is_end / 空列表 / 增量边界），
  而非子进程退出码（中途 break 也是 returncode 0，必须区分「完整完成」与「部分/失败」）。
- 失败重试：本次会话内最多 3 次、间隔退避；重试分叉——无基线→全量重试，有基线→增量续拉
  （每页持久化游标到 meta.sync_cursor，断点续拉）；全部失败→提示"重试失败，等待下次手动同步"。
-     upsert 字段替换规则（§12.2 / §13）：
    view_at      : MAX(excluded, history) （正常增量即最新；MAX 仅防极端乱序）
    progress     : 粘性保护——stored 已看完(-1) 或 stored 手动标记"不需要观看"(manual_skip=1)
                   或 stored 自动标记(auto_skip=1) → 不更新；
                   否则 new.view_at > stored → 用新值；new.view_at == stored（⑥加固）→ 仅当新进度更大时更新；
                   new.view_at < stored（乱序）→ 保留 stored。
    duration     : COALESCE(excluded, history) 保持不变
    title/author/cover/uri/... : 直接更新为当前 B站状态
    archived_only: 保持 stored 值（增量不更新；仅全量基线标记，见 §12.3 选 A）
    manual_skip  : 保持 stored 值（用户本地标记，同步不覆盖）
    auto_skip    : 保持 stored 值（由筛选规则全量重扫生成，同步不覆盖；见 §13）

仅用标准库（urllib / sqlite3 / json / ...），零第三方依赖。

用法：
    python collector.py                  # 智能：有基线→增量；无基线→全量建基线
    python collector.py --full           # 强制全量（重新校准 archived_only / 刷新封面）
    python collector.py --incremental    # 强制增量（无基线时自动转全量）
    python collector.py --limit-pages 3  # 只跑前 3 页，便于联调
    python collector.py --config ../config.json

凭证：在 config.json 填入浏览器 F12 → Application → Cookie → SESSDATA 的值。
"""
import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
DEFAULT_CONFIG = os.path.join(PROJECT_ROOT, "config.json")
DEFAULT_DB = os.path.join(PROJECT_ROOT, "data", "bilibili_history.db")
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "sync_progress.json")
RESULT_FILE = os.path.join(PROJECT_ROOT, "data", "sync_result.json")
API_URL = "https://api.bilibili.com/x/web-interface/history/cursor"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def write_progress(page, fetched, phase, completed=False, is_end=False,
                   total_estimate=None, error=None, mode=None):
    """把同步进度写到 data/sync_progress.json（server 轮询读取，前端展示进度条）。

    total_estimate 是进度条分母的估计值：用已有条数初设，拉取过程中若超出则放大，
    使前端能算出真实百分比（而非无限加载动画）。
    mode 标注本次同步类型：full / incremental / retry，供前端显示不同文字。
    """
    try:
        payload = {
            "phase": phase,
            "page": page,
            "fetched": fetched,
            "completed": completed,
            "is_end": is_end,
            "total_estimate": total_estimate,
            "mode": mode,
            "updated_at": int(time.time()),
        }
        if error is not None:
            payload["error"] = error
        tmp = PROGRESS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, PROGRESS_FILE)
    except Exception:
        pass


def write_result(completed, fetched, total_estimate, error=None):
    """同步结束结论：是否真正完整拉取成功（供 server 判定是否更新数据版本）。

    与子进程退出码解耦——collector 中途 break（网络/接口错）也是 returncode 0，
    必须用此结论区分「完整完成」与「部分/失败」。
    """
    try:
        payload = {
            "completed": completed,
            "fetched": fetched,
            "total_estimate": total_estimate,
            "error": error,
            "finished_at": int(time.time()),
        }
        tmp = RESULT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, RESULT_FILE)
    except Exception:
        pass


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS history (
            kid          TEXT PRIMARY KEY,
            title        TEXT,
            show_title   TEXT,
            long_title   TEXT,
            author_name  TEXT,
            author_mid   TEXT,
            view_at      INTEGER,
            bvid         TEXT,
            oid          TEXT,
            epid         TEXT,
            cid          TEXT,
            business     TEXT,
            cover        TEXT,
            progress     INTEGER,
            duration     INTEGER,
            uri          TEXT,
            tag_name     TEXT,
            badge        TEXT,
            videos       INTEGER,
            raw_json     TEXT,
            created_at   INTEGER,
            updated_at   INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    # 迁移：增量同步 / 归档标记 / 手动标记 / 自动标记所需字段（幂等，列已存在则跳过）
    for ddl in (
        "ALTER TABLE history ADD COLUMN last_seen_sync INTEGER",
        "ALTER TABLE history ADD COLUMN archived_only INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE history ADD COLUMN manual_skip INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE history ADD COLUMN auto_skip INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


def fetch_page(sessdata, ps, max_v, view_at, business):
    params = {"ps": ps, "max": max_v, "view_at": view_at}
    if business:
        params["business"] = business
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": REFERER,
            "Cookie": f"SESSDATA={sessdata}",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body)


def extract_fields(item):
    history = item.get("history") or {}
    covers = item.get("covers") or []
    cover = item.get("cover") or (covers[0] if covers else None)
    kid = item.get("kid")
    if not kid:
        kid = f"{history.get('bvid') or item.get('oid') or ''}_{item.get('view_at')}"
    return {
        "kid": kid,
        "title": item.get("title"),
        "show_title": item.get("show_title"),
        "long_title": item.get("long_title"),
        "author_name": item.get("author_name"),
        "author_mid": str(item["author_mid"]) if item.get("author_mid") is not None else None,
        "view_at": item.get("view_at"),
        "bvid": history.get("bvid") or item.get("bvid"),
        "oid": history.get("oid"),
        "epid": history.get("epid"),
        "cid": history.get("cid"),
        "business": item.get("business") or history.get("business"),
        "cover": cover,
        "progress": item.get("progress"),
        "duration": item.get("duration"),
        "uri": item.get("uri"),
        "tag_name": item.get("tag_name"),
        "badge": item.get("badge"),
        "videos": item.get("videos"),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def upsert(conn, fields, now):
    """按 kid 去重 upsert，落实 §12.2 字段替换规则。

    - 新记录：created_at/updated_at=now，archived_only=0，manual_skip=0。
    - 冲突更新：
        view_at      = MAX(excluded, history)
        progress     = 粘性保护（见下）
        duration     = COALESCE(excluded, history)
        title/author/cover/uri/... = excluded（当前 B站状态）
        archived_only= history.archived_only（增量不更新；全量标记另算）
        manual_skip  = history.manual_skip（用户本地标记，同步不覆盖）
    progress 粘性保护逻辑：
        若 stored.progress == -1（已看完）或 stored.manual_skip == 1（手动标记）
        或 stored.auto_skip == 1（自动标记）→ 保持 stored（不更新）
        否则：
            new.progress IS NULL        → 保持 stored
            new.view_at >  stored.view_at → 用 new（最新观看会话权威）
            new.view_at == stored.view_at → 仅当 new.progress > stored.progress 时更新（⑥ 加固，覆盖"仅进度变、时间未变"盲点）
            new.view_at <  stored.view_at → 保持 stored（乱序防护）
    """
    conn.execute(
        """
        INSERT INTO history (
            kid, title, show_title, long_title, author_name, author_mid,
            view_at, bvid, oid, epid, cid, business, cover, progress,
            duration, uri, tag_name, badge, videos, raw_json,
            created_at, updated_at, last_seen_sync, archived_only, manual_skip, auto_skip
        ) VALUES (
            :kid, :title, :show_title, :long_title, :author_name, :author_mid,
            :view_at, :bvid, :oid, :epid, :cid, :business, :cover, :progress,
            :duration, :uri, :tag_name, :badge, :videos, :raw_json,
            :created_at, :updated_at, :last_seen_sync, 0, 0, 0
        )
        ON CONFLICT(kid) DO UPDATE SET
            title=excluded.title,
            show_title=excluded.show_title,
            long_title=excluded.long_title,
            author_name=excluded.author_name,
            author_mid=excluded.author_mid,
            view_at=MAX(excluded.view_at, history.view_at),
            bvid=excluded.bvid,
            oid=excluded.oid,
            epid=excluded.epid,
            cid=excluded.cid,
            business=excluded.business,
            cover=excluded.cover,
            progress=CASE
                WHEN history.progress = -1 OR history.manual_skip = 1 OR history.auto_skip = 1 THEN history.progress
                WHEN excluded.progress IS NULL THEN history.progress
                WHEN excluded.view_at > history.view_at THEN excluded.progress
                WHEN excluded.view_at = history.view_at THEN
                    CASE WHEN excluded.progress > history.progress THEN excluded.progress
                         ELSE history.progress END
                ELSE history.progress
            END,
            duration=COALESCE(excluded.duration, history.duration),
            uri=excluded.uri,
            tag_name=excluded.tag_name,
            badge=excluded.badge,
            videos=excluded.videos,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at,
            last_seen_sync=excluded.last_seen_sync,
            archived_only=history.archived_only,
            manual_skip=history.manual_skip,
            auto_skip=history.auto_skip
        """,
        {**fields, "created_at": now, "updated_at": now, "last_seen_sync": now},
    )


def _load_cursor(conn):
    """读取持久化的断点续拉游标（上次中断处的 max/view_at/business）。"""
    row = conn.execute("SELECT value FROM meta WHERE key='sync_cursor'").fetchone()
    if not row:
        return None
    try:
        d = json.loads(row[0])
        return (d.get("max", 0), d.get("view_at", 0), d.get("business", "") or "")
    except Exception:
        return None


def _save_cursor(conn, cursor):
    """持久化断点续拉游标。"""
    max_v, view_at, business = cursor
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('sync_cursor',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps({"max": max_v, "view_at": view_at, "business": business}),),
    )
    conn.commit()


def _fetch_loop(conn, sessdata, ps, start_cursor, incremental, last_sync_int,
                limit_pages, now, existing, interval):
    """单趟拉取循环。返回 (completed, err_msg, pages, fetched, added, updated, last_cursor)。

    - 正常结束：cursor.is_end / 空列表 / 增量边界 → completed=True。
    - 错误中断（HTTP/网络/接口错/游标未推进）→ completed=False，err_msg 说明原因。
    - 每页提交 + 每页持久化进度文件，避免中断丢数据；游标实时推进。
    """
    max_v, view_at, business = start_cursor
    pages = fetched = added = updated = 0
    completed = False
    err_msg = None
    total_estimate = existing if existing > 0 else 120
    seen_cur = conn.cursor()

    while True:
        if limit_pages is not None and pages >= limit_pages:
            err_msg = "已达到 --limit-pages 上限（调试用，非完整同步）"
            break
        try:
            data = fetch_page(sessdata, ps, max_v, view_at, business)
        except urllib.error.HTTPError as e:
            log(f"❌ HTTP 错误 {e.code}: {e.reason}")
            err_msg = f"HTTP 错误 {e.code}"
            break
        except urllib.error.URLError as e:
            log(f"❌ 网络错误: {e.reason}")
            err_msg = f"网络错误: {e.reason}"
            break

        if data.get("code") != 0:
            log(f"❌ 接口返回错误 code={data.get('code')} message={data.get('message')}")
            if data.get("code") in (-101, -111):
                log("   → 登录态失效/未登录，请更新 SESSDATA。")
            err_msg = f"接口错误 code={data.get('code')}"
            break

        items = (data.get("data") or {}).get("list") or []
        if not items:
            # 没有更多数据 = 已完整拉取
            completed = True
            break

        for it in items:
            f = extract_fields(it)
            if not f["kid"]:
                continue
            exist = seen_cur.execute(
                "SELECT 1 FROM history WHERE kid=?", (f["kid"],)
            ).fetchone()
            upsert(conn, f, now)
            if exist:
                updated += 1
            else:
                added += 1
            fetched += 1

        pages += 1
        if pages % 10 == 0:
            log(f"已处理 {pages} 页，累计 {fetched} 条…")
        conn.commit()  # 每页提交，避免中断丢数据
        # 估计分母：已拉取数超过估计则放大，使进度条持续推进
        if fetched > total_estimate:
            total_estimate = fetched
        write_progress(pages, fetched, "fetch", completed=False, is_end=False,
                       total_estimate=total_estimate, mode=("incremental" if incremental else "full"))

        cursor = (data.get("data") or {}).get("cursor") or {}
        if cursor.get("is_end"):
            log("已到末尾（cursor.is_end=true），完整拉取完成。")
            completed = True
            break
        n_max = cursor.get("max")
        n_view = cursor.get("view_at")
        n_business = cursor.get("business")
        if n_max == max_v and n_view == view_at:
            log("游标未推进，停止以避免死循环（仅部分同步）。")
            err_msg = "游标未推进（疑似接口异常，仅部分同步）"
            break
        # 增量边界：已越过上次同步时刻，更早的条目不会变化，停止
        if incremental and last_sync_int and n_view and n_view < last_sync_int:
            log(f"到达增量边界（view_at {n_view} < 上次同步 {last_sync_int}），本次增量完成。")
            completed = True
            break
        max_v, view_at, business = n_max, n_view, n_business
        if interval > 0:
            time.sleep(interval)

    return completed, err_msg, pages, fetched, added, updated, (max_v, view_at, business)


def run(config, db_path, limit_pages=None, incremental=False, full=False):
    sessdata = (config.get("SESSDATA") or "").strip()
    ps = int(config.get("page_size", 30))
    interval = float(config.get("request_interval", 0.3))
    conn = init_db(db_path)
    now = int(time.time())

    if not sessdata or sessdata.startswith("在此填入") or len(sessdata) < 10:
        log("⚠️ 未检测到有效 SESSDATA，仅初始化数据库结构后退出。")
        log("   请到浏览器 F12 → Application → Cookie → SESSDATA 取值，粘贴进 config.json 后重试。")
        conn.commit()
        conn.close()
        return 0

    # 上次同步时刻（增量边界 & 归档判定基准）
    last_sync_row = conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
    last_sync_int = int(last_sync_row[0]) if last_sync_row else 0
    existing = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    has_baseline = last_sync_int > 0 and existing > 0

    # 基线判定（§12.1）：全量仅建基线，之后只增量
    mode = "incremental" if incremental else "full"
    if full:
        incremental = False
        mode = "full"
        log("强制全量同步（用于重新校准 archived_only / 刷新封面）。")
    elif incremental and not has_baseline:
        log("无基线（last_sync 不存在或库为空），增量不可用 → 自动转为全量建基线。")
        incremental = False
        mode = "full"

    # 失败重试：最多 3 次尝试，断点续拉
    max_attempts = 3
    attempt = 0
    completed = False
    err_msg = None
    pages = total = added = updated = 0
    last_cursor = (0, 0, "")
    write_progress(0, 0, "start", completed=False, is_end=False,
                   total_estimate=existing if existing > 0 else 120, mode=mode)

    while attempt < max_attempts:
        if attempt > 0:
            log(f"⟳ 第 {attempt} 次重试（断点续拉）…")
            time.sleep(min(5, attempt * 2))  # 退避
            start_cursor = _load_cursor(conn) or (0, 0, "")
            # 重试阶段进度标注为 retry
            write_progress(pages, total, "retry", completed=False, is_end=False,
                           total_estimate=existing if existing > 0 else 120,
                           mode="retry")
        else:
            start_cursor = (0, 0, "")

        c, em, pg, ft, ad, up, lc = _fetch_loop(
            conn, sessdata, ps, start_cursor, incremental, last_sync_int,
            limit_pages, now, existing, interval,
        )
        # 每页间隔：在 _fetch_loop 外控制（避免在子函数里耦合 sleep）
        # 注：为保持与历史行为一致，间隔在循环体通过 interval 处理（见下方）
        pages += pg
        total += ft
        added += ad
        updated += up
        last_cursor = lc

        if c:
            completed = True
            err_msg = None
            break
        # 失败：持久化断点，便于下次重试续拉
        _save_cursor(conn, lc)
        err_msg = em or "同步中断"
        log(f"⚠ 第 {attempt + 1} 次尝试未完成：{err_msg}")
        attempt += 1

    # 全量同步：仅在「真正完整拉取」后才标记归档（避免部分拉取误把有效记录标为存档）
    archived = 0
    if completed:
        if not incremental:
            cur = conn.execute(
                "UPDATE history SET archived_only=1 "
                "WHERE (last_seen_sync IS NULL OR last_seen_sync < ?) AND archived_only=0",
                (now,),
            )
            archived = cur.rowcount
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('last_full_sync',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(now),),
            )
        # 只有完整同步才推进 last_sync（作为下次增量边界；部分同步不推进，便于重试续拉）
        conn.execute(
            "INSERT INTO meta(key,value) VALUES('last_sync',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(now),),
        )
        # 完整同步后清除断点游标（下次从新基线增量）
        conn.execute("DELETE FROM meta WHERE key='sync_cursor'")

        # 同步结论落地（server 轮询读取；前端进度条/完成文字）
        final_estimate = existing if existing > 0 else max(total, 1)
        write_progress(pages, total, "done", completed=True, is_end=True,
                       total_estimate=final_estimate, mode=mode)
        write_result(True, total, final_estimate, None)
        log(f"✅ 同步完成：{pages} 页 / {total} 条（新增 {added}，更新 {updated}，"
            f"标记归档 {archived}）{'[增量]' if incremental else '[全量]'}；写入 {db_path}")
    else:
        write_progress(pages, total, "error", completed=False, is_end=False,
                       total_estimate=existing if existing > 0 else 120,
                       error=err_msg, mode=mode)
        write_result(False, total, existing if existing > 0 else 120, err_msg)
        log(f"⚠ 同步失败（共 {max_attempts} 次尝试）：{err_msg or '未知原因'}（已拉取 {total} 条，不更新数据版本）；请稍后手动重试")

    conn.commit()
    conn.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description="B站历史记录采集器 (同步核心)")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    ap.add_argument("--db", default=None, help="数据库路径（覆盖 config.json 的 db_path）")
    ap.add_argument("--limit-pages", type=int, default=None, help="只跑前 N 页（联调用）")
    ap.add_argument("--incremental", action="store_true",
                    help="增量同步：拉到上次同步时刻即停（无基线时自动转全量）")
    ap.add_argument("--full", action="store_true",
                    help="强制全量同步（重新校准 archived_only / 刷新封面）")
    args = ap.parse_args()

    config = load_config(args.config)
    db_path = args.db or config.get("db_path") or DEFAULT_DB
    if not os.path.isabs(db_path):
        db_path = os.path.join(PROJECT_ROOT, db_path)

    log(f"配置: {args.config}")
    log(f"数据库: {db_path}")
    run(config, db_path, limit_pages=args.limit_pages,
        incremental=args.incremental, full=args.full)


if __name__ == "__main__":
    main()
