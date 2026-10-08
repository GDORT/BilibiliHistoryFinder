# -*- coding: utf-8 -*-
"""B3 全量：为 2020–2025 长尾（363 条）补 `duration`，产出 overlay 文件。

**为什么存 overlay 而不是改库**（2026-10-05 决策）：
  `store._merge_records` 的口径是「**Analyzer 优先**，本地库仅补齐 Analyzer 没有的 kid」。
  2020–2025 的 kid 在 Analyzer 侧**存在**（只是 `duration=0`）→ **补进本地库会被 Analyzer 的 0 盖掉**。
  而 Finder 的定位是**只读 Analyzer**（`adapter_analyzer` 只有 `read_analyzer_records`），
  直接写主库属于破例。所以改成**旁路的 overlay**：`data/duration_backfill.json`，
  由查询层叠加（`overlay 优先，miss 再读源库`）—— 可随时删除、当天生效、不碰任何源库。

**只发 363 个 GET 请求，详情接口不带 SESSDATA**（实测无需登录）→ 零风控额度。
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
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/92.0.4515.107 Safari/537.36",
]
OUT = os.path.join(ROOT, "data", "duration_backfill.json")
TABLES = ["bilibili_history_2020", "bilibili_history_2021", "bilibili_history_2022",
          "bilibili_history_2023", "bilibili_history_2024", "bilibili_history_2025"]


def make_cookie():
    b3 = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    b4 = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    nut = str(int(time.time() * 1000))
    return ("buvid3=%s; buvid4=%s; b_nut=%s; bsource=search_google; _uuid=D%s-%s-%s"
            % (b3, b4, nut, b3, nut, b4))


def query(bvid, tries=3):
    headers = {
        "User-Agent": random.choice(UAS),
        "Referer": "https://www.bilibili.com/video/%s" % bvid,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://www.bilibili.com",
        "Cookie": make_cookie(),
    }
    for i in range(tries):
        try:
            req = urllib.request.Request(API + bvid, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (412, 429):
                time.sleep(4 ** i + random.uniform(1, 3))
                continue
            return {"__error": "HTTP %d" % e.code}
        except Exception as e:
            time.sleep(2)
    return {"__error": "retries exhausted"}


def main():
    p = (os.environ.get("ANALYZER_DB")
         or r"D:\Program Files (x86)\BilibiliHistoryAnalyzer\output\bilibili_history.db")
    con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    rows = []
    for t in TABLES:
        rows += con.execute('SELECT bvid, view_at, title FROM "%s"' % t).fetchall()
    con.close()
    nb = [r for r in rows if r[0]]
    print("=== B3 全量补 duration：%d 条（总长尾 %d，有 bvid %d）===" % (len(nb), len(rows), len(nb)))
    print("  详情接口不带 SESSDATA → 零风控额度")
    print("  间隔 1.0–2.0s，预计 %d 分钟" % round(len(nb) * 1.5 / 60, 1))
    print()

    overlay = {}
    failed = []
    t0 = time.time()
    for i, (bvid, view_at, title) in enumerate(nb, 1):
        r = query(bvid)
        code = r.get("code")
        data = r.get("data") or {}
        dur = data.get("duration") if isinstance(data, dict) else None
        if code == 0 and isinstance(dur, int) and dur > 0:
            overlay[bvid] = dur
            flag = "."
        else:
            failed.append({"bvid": bvid, "code": code,
                           "msg": (r.get("message") or r.get("__error") or "")[:60]})
            flag = "x" if code != 0 else "-"
        sys.stdout.write(flag)
        sys.stdout.flush()
        if i % 25 == 0:
            print(" [%d/%d] ok=%d fail=%d  %.0fs" % (i, len(nb), len(overlay), len(failed), time.time() - t0))
        time.sleep(random.uniform(1.0, 2.0))
    print()
    print()
    print("=== 结果 ===")
    print("  查询 %d 条 ｜ 成功拿到 duration %d 条（%.1f%%）｜ 失败 %d 条"
          % (len(nb), len(overlay), 100.0 * len(overlay) / max(len(nb), 1), len(failed)))
    print("  耗时 %.1f 分钟" % ((time.time() - t0) / 60))
    if failed:
        print("  失败样本（前 8）：")
        for f in failed[:8]:
            print("    %-14s code=%-8s %s" % (f["bvid"], f["code"], f["msg"]))
    print()
    d = sorted(overlay.values())
    if d:
        print("  duration 分布：min=%d 中位=%d max=%d ｜ <60s 的 %d 条"
              % (d[0], d[len(d) // 2], d[-1], sum(1 for x in d if x < 60)))
    print()
    payload = {
        "_comment": ("2026-10-05 由 dev/ 脚本按 bvid 查 B站详情接口补 2020-2025 长尾的 duration。"
                     "详情接口不带 SESSDATA。**overlay 语义**：查询时 kid 命中本表则用此 duration，"
                     "miss 再读源库。删除本文件即完全回退。"),
        "_source": "https://api.bilibili.com/x/web-interface/view?bvid=",
        "_created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "_count": len(overlay),
        "_failed": len(failed),
        "map": overlay,
    }
    io.open(OUT, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=1))
    print("  已写：%s（%d 条，%d B）" % (OUT, len(overlay), os.path.getsize(OUT)))


if __name__ == "__main__":
    main()
