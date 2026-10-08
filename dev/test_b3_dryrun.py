# -*- coding: utf-8 -*-
"""B3：`#40` 长尾 `duration` 补全 —— **dry-run 10 个 bvid**。

关键前提（2026-10-05 实测确认）：
  - 详情接口 `https://api.bilibili.com/x/web-interface/view?bvid=xxx`
    **无需 SESSDATA**（Analyzer 自己的实现也刻意不带，只用 buvid3 模拟头）
    → 本脚本**不发你的 Cookie**，零风控额度
  - 2020–2025 的 363 条年表记录**全部有 bvid**、全部 `business='archive'`

本脚本**只读**：查到的 duration 只打印，**不写任何库**。
"""
import io
import os
import sys
import json
import time
import random
import string
import sqlite3
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.bilibili.com/x/web-interface/view?bvid="
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/92.0.4515.159 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/14.1.1 Safari/605.1.15",
]
N = 10


def make_cookie():
    b3 = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    b4 = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    nut = str(int(time.time() * 1000))
    return ("buvid3=%s; buvid4=%s; b_nut=%s; bsource=search_google; _uuid=D%s-%s-%s"
            % (b3, b4, nut, b3, nut, b4))


def query(bvid, tries=3):
    ua = random.choice(UAS)
    b3 = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    headers = {
        "User-Agent": ua,
        "Referer": "https://www.bilibili.com/video/%s" % bvid,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.bilibili.com",
        "Cookie": make_cookie(),
    }
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(API + bvid, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
            return body
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
            if e.code in (412, 429):
                time.sleep(4 ** i + random.uniform(1, 3))
                continue
            return {"__error": last}
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(2)
    return {"__error": last}


def main():
    p = os.environ.get("ANALYZER_DB") or r"D:\Program Files (x86)\BilibiliHistoryAnalyzer\output\bilibili_history.db"
    con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    rows = []
    for t in ("bilibili_history_2020", "bilibili_history_2021", "bilibili_history_2022",
              "bilibili_history_2023", "bilibili_history_2024", "bilibili_history_2025"):
        rows += con.execute('SELECT bvid, view_at, progress, business, title FROM "%s"' % t).fetchall()
    con.close()
    nb = [r for r in rows if r[0]]
    print("=== B3 dry-run：%d 个 bvid（总长尾 %d 条，有 bvid %d 条）===" % (N, len(rows), len(nb)))
    print("  ★ 详情接口**不带 SESSDATA** → 零风控额度、不会碰你的登录态")
    print()
    sample = random.sample(nb, min(N, len(nb)))
    ok = 0
    got = 0
    for i, (bvid, view_at, prog, biz, title) in enumerate(sample, 1):
        r = query(bvid)
        code = r.get("code")
        data = r.get("data") or {}
        dur = (data.get("duration") if isinstance(data, dict) else None)
        flag = ""
        if code == 0 and dur:
            ok += 1
            got += 1
            flag = "✅"
        elif code == 0:
            ok += 1
            flag = "⚠️ 无 duration"
        else:
            flag = "❌ %s" % (r.get("message") or r.get("__error") or code)
        print("  %s [%2d/%2d] %-14s code=%-6s dur=%-7s %s"
              % (flag, i, len(sample), bvid, code, dur, (title or "")[:26]))
        time.sleep(random.uniform(1.2, 2.4))   # 降低风控概率
    print()
    print("=== 结果 ===")
    print("  查询成功 %d/%d ｜ 拿到 duration %d 条" % (ok, len(sample), got))
    print("  ⇒ 若成功率 100%%，全量 363 条约需 %d 分钟（含每次 1.2–2.4s 间隔）"
          % round(363 * 1.8 / 60, 1))


if __name__ == "__main__":
    main()
