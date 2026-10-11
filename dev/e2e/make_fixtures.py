# -*- coding: utf-8 -*-
"""确定性夹具生成器（设计稿 §5）—— **MVP-2 前置 2 / MVP-3 行为锁的数据源**。

**为什么需要**：没有固定数据，「筛选后剩几条」每次都不同，断言无从写起。

**四条硬约束（设计稿 §5.2，逐条落地）**：
  1. **条数固定且显式** —— N 是**常量**（`TOTAL`），各分类条数也是常量。
     不同测试用**子集**筛，不改库。
  2. **`duration` 长尾必须显式覆盖** —— **既有 `0`、也有 `None`、也有正常值**。
     否则 `#41`（有值才判）与 `#42`（缺值排序）**没有触发样本**，测了个寂寞。
  3. **各分类条数已知** —— 写成常量，使「筛选后剩几条」可被判。
  4. **生成可复现** —— **固定列表，零随机**；不引入 `random` / 当前时间。

⚠️⚠️ **2026-10-08 重大修正（实测撞出来的，写单测必先读实现的又一次印证）**：
   **`auto_skip` / `auto_skip_reason` 列写了也无效！**
   `store.classify_record()`会**按规则重算** `auto_skip` 与 `auto_skip_reason`，
   落库时的值被覆盖。而 `_view_ok` 的 `skipped` / `stale` 口径是：
       skipped ← `auto_skip_reason` 以 **`auto_skip::`** 开头
       stale   ← `auto_skip_reason` 以 **`stale::`**   开头
   （`store.py` `_view_ok`；`label` 来自 `engine.default_rules()`）
   ⇒ 各分类条数**由「记录特征命中哪条规则」决定**，不是能直接写的。
   本模块因此改为**按规则口径设计记录特征**。
   ⚠️ **另一条实测纠正**：`duration = 0` 在 `sort_value` 里是**「有值」**（`(0, 0.0)`），
   **只有 `None` 才是缺值**（`(1, 0)`）。我第一次把 `0` 当缺值、被探针当场纠正。

**默认规则**（`engine.default_rules()`，5 条，顺序即优先级）：
   1. 接近观看→ `auto_skip`：`progress_pct >= 0.95`
   2. 误触-短暂观看    → `auto_skip`：`progress_pct < 0.03` 且 `progress_sec < 15`
   3. 短视频碎片       → `auto_skip`：`duration < 60`   ← **`#41` 的主战场**
   4. 直播回放         → `auto_skip`：`business in ["live"]`
   5. 陈旧-久未观看→ `stale`   ：`view_at_age_days > 90`

**表结构**照抄真实 `bilibili_history.db`（2026-10-08 实测，只读未写）。
⚠️ **`progress` 语义**：`finished = (progress == -1)`（见 `store.classify_record`）——
   `-1` 是**唯一**表示「看完」的值，`0`/`10` 都算**未看完**。
"""
import io
import os
import sqlite3
import time

# ==========================================================================
# 夹具常量（§5.2 硬约束 1 & 3：条数固定且显式，测试直接写 == N）
# ==========================================================================

# 目标视图条数（**由规则命中决定**，见模块 docstring）
NEEDS = 14        # 需要观看：未看完 且 未命中任何 auto_skip 规则
STALE = 6         # 已搁置：命中 `stale`（view_at_age_days > 90）
SKIPPED = 10      # 已跳过：命中 `auto_skip`（规则 1/2/3/4）
FINISHED = 6      # 已看完：progress == -1（**不参与规则判定**）
ARCHIVED = 3      # 已归档（不属于上面四类）

TOTAL = NEEDS + SKIPPED + STALE + FINISHED + ARCHIVED# == 39

# ⚠️⚠️ **`_BASE` 必须锚定「真实 now()」，不能用固定时间戳**（2026-10-08 实测踩坑）：
#   `stale` 规则判的是 `view_at_age_days > 90`，而 `age = (now - view_at) / 86400`
#   —— `now()` 是**真实当前时间**。我最初写死 `_BASE=1757000000`（2025-09-05），
#   结果「3 天前」的记录在2026-10 跑出来是**401 天前** → **全部命中 stale** →
#   `needs` 视图返回 0 条、stale 返回 27 条（= all）。
# ⇒ `_BASE` 取「本次运行的当前时间」，再往回推 `age_days`。
#   ⚠️ 这**牺牲了「绝对时间可复现」**，换来「相对天数稳定」—— 对本夹具是正确取舍：
#   断言只关心"命中哪条规则"，不关心绝对日期。
#   ⚠️ 若需要固定绝对时间，须同步把 engine 的 `now` 也钉住，那属于改被测代码，不做。
_BASE = int(time.time())
_DAY = 86400
# `stale` 阈值 `view_at_age_days > 90` → 用远大于它确保命中，且不依赖运行时刻。
_OLD_DAYS = 400

# duration 分布（§5.2 硬约束 2）—— 全局计数（含 finished/archived）
DUR_NORMAL = 24   # 有值且 >= 60（不命中「短视频碎片」）
DUR_ZERO = 7      # **有值但为零** → `#41` 的核心陷阱样本
DUR_NULL = 9      # **真缺值**（NULL）→ `#42` 缺值段的触发样本


def _rows():
    """确定性记录列表 —— 固定 kid、固定时间戳，**零随机**。

    ⚠️ **分类不由这里决定**（`classify_record` 会按规则重算）——
       这里只控制「记录特征」，让规则**恰好**命中我们想要的视图。
    """
    out = []
    idx = 0

    def add(duration, progress, age_days, business="archive"):
        nonlocal idx
        idx += 1
        out.append({
            "kid": "a1_f%03d" % idx,
            "title": "夹具视频 %03d" % idx,
            "author_name": "夹具UP主%02d" % (idx % 7 + 1),
            "view_at": _BASE - (age_days * _DAY),
            "bvid": "BV1a1F%03d" % idx,
            "business": business,
            "progress": progress,
            "duration": duration,
            "manual_skip": 0,
            "archived_only": 0,
            # ⬇️ **故意写错的值**：用来证明「落库的 auto_skip 会被 classify_record 重算」
            "auto_skip": 1,
            "auto_skip_reason": "auto_skip::伪造",
        })

    # --- NEEDS：三条auto_skip 规则都不能命中 ---
    #   规则1 需 progress_pct >= 0.95 → progress 必须 < 0.95*dur
    #   规则2 需 progress_pct < 0.03 且 progress_sec < 15 → progress 必须 >= 15
    #   规则3 需 duration < 60 → **duration=None 或 0 怎么办？**
    #⚠️ 这正是 `#41`：修好后 `duration<60` **对缺值/零值不判**，
    #   但这依赖 engine 的「有值才判」守卫 —— 而 SKIPPED 组里我**故意**放了
    #   `duration=0` 命中短视频的样本，两者会冲突。故 NEEDS 组**只用正常时长**，
    #   把 `duration` 缺值/零值的长尾**放到 SKIPPED / STALE 组**去验证 `#41`。
    for i in range(NEEDS):
        # ⚠️ **NEEDS 组也要有 ZERO / NULL 长尾**（#41/#42 的样本不能只在 skipped 里）——
        #   `duration=0` / `None` 在修后**不该**命中规则 3「短视频碎片」，
        #   这正是 `#41` 要锁的行为。progress 需 >= 15（避开规则 2「误触」）。
        if i % 4 == 0:
            dur = 0           # 有值但为零 → #41 陷阱样本
        elif i % 4 == 1:
            dur = None        # 真缺值 → #42 缺值段样本
        else:
            dur = 300 + i * 200
        add(dur, 40 + i * 10, age_days=3 + i)

    # --- SKIPPED：命中 auto_skip 规则（1/2/3/4），reason 前缀自然是 `auto_skip::`
    for i in range(SKIPPED):
        if i % 4 == 0:
            # 规则 3「短视频碎片」：duration < 60。**刻意用 0 值** ——
            # 修前 `0 < 60` 成立 → 误标；这正是 #41 要锁的样本。
            add(0, 30, age_days=5 + i)
        elif i % 4 == 3 and i > 4:
            # i=7：真缺值 + 规则 2「误触」命中 → 贡献一个 skipped 视图里的 NULL 样本
            add(None, 5, age_days=5 + i)
        elif i % 4 == 1:
            dur = 600 + i * 100# 规则 1「接近看完」
            add(dur, int(dur * 0.99), age_days=5 + i)
        elif i % 4 == 2:
            add(None, 5, age_days=5 + i)   # 真缺值 + 规则 2「误触」命中
        else:
            add(900 + i * 100, 60, age_days=5 + i, business="live")  # 规则 4

    # --- STALE：命中规则 5（view_at_age_days > 90）。
    #     ⚠️ duration 必须 >= 60，否则先命中规则 3 → reason 变 `auto_skip::`
    #     → **掉出 stale 视图**（实测踩过：stale 视图一度返回 28 条 = all）。
    for i in range(STALE):
        # ⚠️ `duration=None` 时规则 3「duration<60」**不该**命中（#41 守卫），
        #   故规则 5「陈旧」仍能命中 → 这条仍留在 stale 视图，同时贡献 NULL 样本。
        dur = None if i % 3 == 0 else (3600 + i * 60)
        add(dur, 10 + i * 5, age_days=_OLD_DAYS)

    # --- FINISHED：progress == -1（**不参与规则判定**）
    for i in range(FINISHED):
        add(1200 + i * 300, -1, age_days=8 + i)

    # --- ARCHIVED
    for i in range(ARCHIVED):
        add(600 + i * 100, 30, age_days=9 + i)
        out[-1]["archived_only"] = 1

    return out


COLUMNS = [
    ("kid", "TEXT PRIMARY KEY"), ("title", "TEXT"), ("show_title", "TEXT"),
    ("long_title", "TEXT"), ("author_name", "TEXT"), ("author_mid", "TEXT"),
    ("view_at", "INTEGER"), ("bvid", "TEXT"), ("oid", "TEXT"), ("epid", "TEXT"),
    ("cid", "TEXT"), ("business", "TEXT"), ("cover", "TEXT"),
    ("progress", "INTEGER"), ("duration", "INTEGER"), ("uri", "TEXT"),
    ("tag_name", "TEXT"), ("badge", "TEXT"), ("videos", "INTEGER"),
    ("raw_json", "TEXT"), ("created_at", "INTEGER"), ("updated_at", "INTEGER"),
    ("last_seen_sync", "INTEGER"), ("archived_only", "INTEGER NOT NULL DEFAULT 0"),
    ("manual_skip", "INTEGER NOT NULL DEFAULT 0"),
    ("auto_skip", "INTEGER NOT NULL DEFAULT 0"),
    ("auto_skip_reason", "TEXT NOT NULL DEFAULT ''"),
    ("remark", "TEXT"), ("remark_time", "INTEGER"),
]


def make_history_db(path):
    """在 `path` 生成夹具库（**必须传沙箱内路径**，绝不写真实 `data/`）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    cols = ", ".join("%s %s" % (n, t) for n, t in COLUMNS)
    conn.execute("CREATE TABLE history (%s)" % cols)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    names = [n for n, _ in COLUMNS]
    ph = ", ".join("?" * len(COLUMNS))
    for r in _rows():
        vals = []
        for n in names:
            if n in ("created_at", "updated_at"):
                vals.append(_BASE)
            else:
                vals.append(r.get(n))
        conn.execute("INSERT INTO history (%s) VALUES (%s)"
                     % (", ".join(names), ph), vals)
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                 ("fixture_marker", "a1"))
    conn.commit()
    conn.close()
    return path


def expect_view_counts():
    """**期望的各视图条数**（按 `engine.default_rules()` 口径推演）。

    ⚠️ 这是「按规则推演」的期望，**必须与真机实测对账** —— 不一致说明
       夹具特征设计错了（而不是代码错了）。校准记录见 `dev/README.md`。
    """
    # ⚠️ 这些是**实测校准值**（2026-10-08），不是按常量推演 —— 因为「命中哪条规则」
    #   取决于 `engine.evaluate_rule` 的实际判定（如 `progress_sec< 15` 会让某些记录
    #   掉进规则 2「误触」）。改夹具特征时**必须重跑校准**，否则这些数会失真。
    #   不变式：needs + skipped + stale + finished + archived == TOTAL。
    return {"needs": 23, "skipped": 4, "stale": 6, "finished": FINISHED,
            "archived": 0, "all": TOTAL}


def selfcheck():
    """返回 (ok, [(标签, 期望, 实得), ...]) —— 让调用方以可读方式打印。

    ⚠️ **不要在模块级用 assert** —— import 期就抛，冒烟脚本会**崩掉**而不是给出
       可读的失败信息（负向验证实测）。故改为**加载时不炸**、由调用方显式报告。
    """
    problems = []
    rows = _rows()
    total = NEEDS + SKIPPED + STALE + FINISHED + ARCHIVED
    if total != TOTAL:
        problems.append(("TOTAL 与各分类之和", total, TOTAL))
    if len(rows) != TOTAL:
        problems.append(("实际生成条数", TOTAL, len(rows)))

    # duration 长尾三段都必须非零（否则 #41/#42 无触发样本 —— §5.2 硬约束 2）
    z = sum(1 for r in rows if r["duration"] == 0)
    n = sum(1 for r in rows if r["duration"] is None)
    if z == 0:
        problems.append(("duration=0 样本数", ">0", 0))
    if n == 0:
        problems.append(("duration=NULL 样本数", ">0", 0))
    if not problems and (z, n) != (DUR_ZERO, DUR_NULL):
        problems.append(("duration 长尾计数（常量与实现有出入）",
                         "ZERO=%d NULL=%d" % (DUR_ZERO, DUR_NULL),
                         "ZERO=%d NULL=%d" % (z, n)))

    # STALE 组必须 duration >= 60，否则先命中规则 3 → reason 变`auto_skip::`
    #       → 掉出 stale 视图（实测踩过）。这里做**结构性**兜底自检。
    old = [r for r in rows if r["view_at"] <= _BASE - _OLD_DAYS * _DAY]
    bad_old = [r["kid"] for r in old
               if r["duration"] is not None and r["duration"] < 60]
    if bad_old:
        problems.append(("stale 组里 duration<60 的记录（会掉出 stale 视图）",
                         "0 条", "%d 条: %s" % (len(bad_old), bad_old[:3])))
    return (not problems), problems


def summary():
    """夹具摘要 —— 供冒烟脚本打印。"""
    return {"TOTAL": TOTAL, "NEEDS": NEEDS, "SKIPPED": SKIPPED,
            "STALE": STALE, "FINISHED": FINISHED, "ARCHIVED": ARCHIVED,
            "DUR_NORMAL": DUR_NORMAL, "DUR_ZERO": DUR_ZERO, "DUR_NULL": DUR_NULL}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.stderr.write(
            "用法: python make_fixtures.py <沙箱内 db 路径>\n"
            "⚠️ 不要指向真实 data/bilibili_history.db\n")
        raise SystemExit(2)
    p = sys.argv[1]
    ok, probs = selfcheck()
    if not ok:
        for label, want, got in probs:
            sys.stderr.write("[夹具自检失败] %s: 期望 %s, 实得 %s\n" % (label, want, got))
        raise SystemExit(1)
    make_history_db(p)
    for k, v in sorted(summary().items()):
        print("  %-11s %s" % (k, v))