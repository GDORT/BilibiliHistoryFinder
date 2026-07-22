#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站历史记录采集器 — Phase 1（仅数据）

拉取全量观看历史 → 本地 SQLite 永久留存。
- 仅用 Python 标准库（urllib / sqlite3 / json / ...），零第三方依赖。
- 按 kid 去重；同一视频二次观看时覆盖更新 progress / view_at。
- 一次性拉全后续步骤所需字段（cover URL、business、oid、bvid、duration 等），
  整条 raw_json 兜底，避免以后加功能时"当初没拉"要重刷。
- 不做展示、不下封面（那是 Phase 2 / Phase 3 的事）。

用法：
    python collector.py                  # 全量/增量拉取（按 kid 去重 + progress 覆盖更新）
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
API_URL = "https://api.bilibili.com/x/web-interface/history/cursor"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


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
    conn.execute(
        """
        INSERT INTO history (
            kid, title, show_title, long_title, author_name, author_mid,
            view_at, bvid, oid, epid, cid, business, cover, progress,
            duration, uri, tag_name, badge, videos, raw_json,
            created_at, updated_at
        ) VALUES (
            :kid, :title, :show_title, :long_title, :author_name, :author_mid,
            :view_at, :bvid, :oid, :epid, :cid, :business, :cover, :progress,
            :duration, :uri, :tag_name, :badge, :videos, :raw_json,
            :created_at, :updated_at
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
                WHEN excluded.progress IS NULL THEN history.progress
                WHEN excluded.progress = -1 OR excluded.progress > history.progress THEN excluded.progress
                ELSE history.progress
            END,
            duration=COALESCE(excluded.duration, history.duration),
            uri=excluded.uri,
            tag_name=excluded.tag_name,
            badge=excluded.badge,
            videos=excluded.videos,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        {**fields, "created_at": now, "updated_at": now},
    )


def run(config, db_path, limit_pages=None):
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

    max_v, view_at, business = 0, 0, ""
    pages, total = 0, 0
    while True:
        if limit_pages is not None and pages >= limit_pages:
            break
        try:
            data = fetch_page(sessdata, ps, max_v, view_at, business)
        except urllib.error.HTTPError as e:
            log(f"❌ HTTP 错误 {e.code}: {e.reason}")
            break
        except urllib.error.URLError as e:
            log(f"❌ 网络错误: {e.reason}")
            break

        if data.get("code") != 0:
            log(f"❌ 接口返回错误 code={data.get('code')} message={data.get('message')}")
            if data.get("code") in (-101, -111):
                log("   → 登录态失效/未登录，请更新 SESSDATA。")
            break

        items = (data.get("data") or {}).get("list") or []
        if not items:
            break

        for it in items:
            f = extract_fields(it)
            if not f["kid"]:
                continue
            upsert(conn, f, now)
            total += 1

        pages += 1
        if pages % 10 == 0:
            log(f"已处理 {pages} 页，累计 {total} 条…")
        conn.commit()  # 每页提交，避免中断丢数据

        cursor = (data.get("data") or {}).get("cursor") or {}
        if cursor.get("is_end"):
            log("已到末尾（cursor.is_end=true）。")
            break
        n_max = cursor.get("max")
        n_view = cursor.get("view_at")
        n_business = cursor.get("business")
        if n_max == max_v and n_view == view_at:
            log("游标未推进，停止以避免死循环。")
            break
        max_v, view_at, business = n_max, n_view, n_business
        if interval > 0:
            time.sleep(interval)

    log(f"完成：共 {pages} 页，{total} 条记录写入 {db_path}")
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('last_sync',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(int(time.time())),),
    )
    conn.commit()
    conn.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description="B站历史记录采集器 (Phase 1)")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    ap.add_argument("--db", default=None, help="数据库路径（覆盖 config.json 的 db_path）")
    ap.add_argument("--limit-pages", type=int, default=None, help="只跑前 N 页（联调用）")
    args = ap.parse_args()

    config = load_config(args.config)
    db_path = args.db or config.get("db_path") or DEFAULT_DB
    if not os.path.isabs(db_path):
        db_path = os.path.join(PROJECT_ROOT, db_path)

    log(f"配置: {args.config}")
    log(f"数据库: {db_path}")
    run(config, db_path, limit_pages=args.limit_pages)


if __name__ == "__main__":
    main()
