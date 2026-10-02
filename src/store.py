#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 数据层（可移植）：把多源历史接进续看规则引擎。

设计要点（对应正式版方案 §3.3 / §4 / §6.2）：
- **主源 = Analyzer SQLite（read-only 直连）**：34 列、2020–2026 深历史，跨年表 UNION，排除 _fts 虚拟表。
- **备份源 = 本地 Finder 库（read-only）**：collector.py 拉取的近期窗口；与主源按 kid 合并（主源优先，本地补齐主源没有的）。
- **分类层（auto_skip / stale / manual_skip）绝不回写源库**：仅我们的规则引擎私有状态落 side DB
  （data/canonical_state.db），与源库解耦、互不污染。
- 分类由 engine 确定性计算（规则命中即 auto_skip），manual_skip / auto_exempt（用户取消自动跳过）持久化在 side DB。
- 全程不 import collector、不直接写 Analyzer / Finder 源库；符合「源库只读」原则。
"""
import hashlib
import json
import os
import sqlite3
import sys
import time

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# Analyzer 库路径（可用环境变量 ANALYZER_DB 覆盖，便于迁移）
ANALYZER_DB = os.environ.get(
    "ANALYZER_DB",
    r"D:\Program Files (x86)\BilibiliHistoryAnalyzer\output\bilibili_history.db",
)
# 本地 Finder 库（备份源，collector.py 写入）
LOCAL_DB = os.path.join(DATA_DIR, "bilibili_history.db")
# 侧状态库（规则引擎私有：manual_skip / auto_exempt / 应用哈希）
STATE_DB = os.path.join(DATA_DIR, "canonical_state.db")

# 从 Analyzer 基表选取的 canonical 字段（34 列中的规则相关子集 + 展示用）
# 扩展：加入 remark / is_fav / videos，供高级筛选器使用（按表安全选列见 _read_analyzer）。
ANALYZER_COLS = [
    "id", "kid", "bvid", "title", "business", "author_name", "author_mid",
    "view_at", "progress", "duration", "cover", "uri", "oid", "dt", "tag_name",
    "live_status", "main_category", "remark", "is_fav", "videos",
]


# ===================== 失败可观测（N-H1 · 收窄版） =====================
# 背景（用户审查稿 N-H1）：全仓 110+ 处 `except` 直接吞掉，于是**「读失败」与「真的没有数据」
# 在表现上完全相同**（都是空列表 / 默认值），故障现场被抹平成"没数据"，是"看起来对、其实不对"
# 的最大放大器。
#
# 本层**只做记录**，刻意保持最小侵入，三条硬约束：
#   1. **不改变任何返回语义**（成功路径一行不动；失败仍返回原来的兜底值）；
#   2. **不抛异常、不写新文件**（只 print 一行 + 进内存环形缓冲，供进程内自省与断言）；
#   3. **不追平 124 处** —— 只覆盖"源读取 + 配置写入"这条链。
#
# 覆盖范围（C-N8，2026-10-01 更正：此前注释写"本文件 3 点 + server.py 6 点"，名单与总数都对不上，
# 而它又被 `doc/log/2026-10-01-待办轮次流水（至第二十轮）.md` 第十九轮照抄，形成"代码注释 → 文档"的错误传播链）：
#   - **直接调用 `note_failure`**：本文件 4 处 —— `_read_analyzer` · `_read_local` ·
#     `_write_json_atomic` · `save_policy`(读)；`server.py` 6 处 —— `_write_json_file` ·
#     `get_db_path` · `get_web_port` · `_load_fetcher_override` · `_load_source_config` ·
#     `_persist_source_config`(读盘失败)。
#   - **经 `_warn_fs_error` 间接调用**（文件系统类，`FileNotFoundError` 不记）：本文件 5 处 ——
#     `local_span_days` · `analyzer_span_days`(list_tables / 单张年表 / 外层) · `local_meta`。
#   - **只经 `_write_json_file` 间接**：`server._persist_fetcher_override`（写盘侧）
#     —— 它自己不调用，故**不算"接入点"**（这正是上面那处名单错的成因）。
#
# 反例（必须**不**记录）：文件不存在是**正常状态**（首次运行 / 还没配 Analyzer 库），
# 只有"文件在、却打不开 / 解析失败 / 结构不符"才算失败。见各处 `except FileNotFoundError` 分支。
FAILURES = []          # 环形缓冲：{"at": ts, "where": str, "error": str}
FAILURES_MAX = 50


def note_failure(where, exc=None, detail=""):
    """记录一次「本应成功却失败」的事件。**绝不抛异常、永不返回失败**（返回 None）。"""
    try:
        if exc is None:
            err = str(detail or "")
        else:
            err = "%s: %s" % (type(exc).__name__, exc)
        FAILURES.append({"at": int(time.time()), "where": str(where), "error": err})
        del FAILURES[:-FAILURES_MAX]
        if sys.stderr is not None:      # pythonw（GUI 子系统）下 stderr 为 None
            sys.stderr.write("[BHF][warn] %s —— %s\n" % (where, err))
            sys.stderr.flush()
    except Exception:
        pass
    return None


def recent_failures():
    """最近若干条失败（拷贝，供自省 / 测试断言）。"""
    return [dict(x) for x in FAILURES]


def clear_failures():
    del FAILURES[:]


def _warn_fs_error(where, exc, path):
    """文件系统类失败的统一记法：**FileNotFoundError 属正常态，不记**。"""
    if isinstance(exc, FileNotFoundError):
        return None
    return note_failure(where, exc, "path=%s" % path)


def _list_base_year_tables(cur):
    """枚举 bilibili_history_YYYY 基表，排除 _fts 全文虚拟表。"""
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name GLOB 'bilibili_history_[0-9][0-9][0-9][0-9]' ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def _rec_key(d):
    """统一合并键 / skip 标识：优先 (bvid,view_at)，回退 (oid,view_at)，再回退 (kid,view_at)。

    实测：Analyzer 与 Finder 对同一视频的 `oid` 不一致，但 `bvid` 稳定一致，且 (bvid,view_at)
    重叠 2389 条；而 `kid` 列 Analyzer 恒为 0、Finder 为 oid（无 view_at）—— 故必须以 (bvid,view_at)
    为合并键才能正确去重。该键同时覆盖输出 kid 字段，保证 skip 持久化跨源一致。
    """
    bvid = d.get("bvid")
    oid = d.get("oid")
    kid = d.get("kid")
    va = d.get("view_at")
    if bvid:
        return f"{bvid}_{va}"
    if oid:
        return f"{oid}_{va}"
    return f"{kid}_{va}"


def _read_analyzer(db_path):
    """read-only 跨年 UNION 读取 Analyzer 全量记录 → {kid: canonical dict}。

    按表安全选列：每张年表只 SELECT 其实际存在的列（remark/is_fav/videos 等
    可能仅新版本 Analyzer 才有），避免 PRAGMA 缺失列导致整表读取失败。

    N-H1：`文件不存在` = 正常态（还没配 Analyzer）→ 直接返回 `{}`，**不记失败**；
    其余异常（库损坏 / 结构不符 / 打不开）→ 记一条带路径的失败后**原样重抛**
    （保持既有语义：该抛的照样抛，只是不再无声）。
    """
    if not os.path.exists(db_path):
        return {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.text_factory = str
        try:
            cur = con.cursor()
            tables = _list_base_year_tables(cur)
            recs = {}
            for tb in tables:
                cur.execute(f"SELECT name FROM pragma_table_info('{tb}')")
                have = {r[0] for r in cur.fetchall()}
                sel = [c for c in ANALYZER_COLS if c in have]
                for row in cur.execute(f"SELECT {', '.join(sel)} FROM {tb}"):
                    d = dict(zip(sel, row))
                    d.setdefault("remark", "")
                    d.setdefault("is_fav", 0)
                    d.setdefault("videos", 1)
                    d["archived_only"] = 0  # Analyzer 无此列，按 0 补足
                    d["source"] = "analyzer"
                    d["kid"] = _rec_key(d)  # 覆盖无效 kid 列，统一复合键
                    recs[d["kid"]] = d
            return recs
        finally:
            con.close()
    except Exception as e:
        note_failure("store._read_analyzer", e, "path=%s" % db_path)
        raise


def _read_local(db_path):
    """read-only 读取本地 Finder 库（备份源）→ {kid: canonical dict}。

    N-H1：与 `_read_analyzer` 同口径 —— 文件不存在是正常态；其余异常记一条失败后原样重抛。
    """
    if not os.path.exists(db_path):
        return {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.text_factory = str
        try:
            cur = con.cursor()
            cur.execute("SELECT name FROM pragma_table_info('history')")
            have = {r[0] for r in cur.fetchall()}
            base = ["kid", "title", "author_name", "author_mid", "view_at", "bvid", "oid",
                    "business", "cover", "progress", "duration", "uri", "archived_only",
                    "raw_json",
                    # 阶段 0 P2 迁移带进来的备注列（老库没有时由 pragma 过滤自动跳过）
                    "remark", "remark_time"]
            sel = [c for c in base if c in have]
            recs = {}
            for row in cur.execute(f"SELECT {', '.join(sel)} FROM history"):
                d = dict(zip(sel, row))
                raw = d.pop("raw_json", None)
                if raw:
                    try:
                        rj = json.loads(raw)
                        d["dt"] = (rj.get("history") or {}).get("dt")
                        d["live_status"] = rj.get("live_status")
                    except Exception:
                        pass
                d.setdefault("archived_only", 0)
                d["source"] = "local"
                d["kid"] = _rec_key(d)  # 统一复合键（与 Analyzer 同格式）
                recs[d["kid"]] = d
            return recs
        finally:
            con.close()
    except Exception as e:
        note_failure("store._read_local", e, "path=%s" % db_path)
        raise


def _fold_latest_by_bvid(records):
    """Plan B：同一 bvid 折叠为一条 —— **展示取「最近一次」，判断取「最有利」**。

    背景：B站历史接口每次「观看会话」记一条独立记录（各自 view_at），同一视频
    多次打开 → 多条。Analyzer 与开源 Frontend 均保留全部会话、展示层不折叠。

    ⚠️ Plan A（只取 view_at 最大一条）有**功能性缺陷**：实测 7 条视频
    「历史某次 progress == -1（已看完）、最近一次只点开没看完」，
    折叠后丢掉 -1 → 被误判为「需要观看」。故改为下面的**状态聚合**：

    - 展示字段（标题 / 封面 / 作者 / uri…）取 `view_at` 最大的一条 = 「最近一次观看」；
    - `progress` 取**最有利值**：任一会话 `== -1` → 结果 `-1`；否则取 `max`；
    - `duration` 取 `max`（防御性；实测与最近一条一致）；
    - 新增 `session_count` / `first_view_at` / `kids`（全部会话的 kid 列表）；
    - 记录自身的 `kid` 仍是「最近一次」的 kid → 展示与既有接口零改动；
    - 无 `bvid` 的记录（直播/专栏/pgc 等）不参与折叠，原样保留。

    配合 `classify_record` 的 `kids` 多键命中（**同批改动，缺一不可**）：
    skip / archived 只要命中任一会话即生效，因此「同一视频再看一次产生新会话」
    不会让既有跳过状态失效（修 Plan A 遗留的 P2 漂移）。
    """
    groups = {}
    order = []
    rest = []
    for r in records:
        bvid = r.get("bvid")
        if not bvid:
            rest.append(r)
            continue
        if bvid not in groups:
            groups[bvid] = []
            order.append(bvid)
        groups[bvid].append(r)

    out = []
    for bvid in order:
        sess = sorted(groups[bvid], key=lambda x: int(x.get("view_at") or 0))
        if len(sess) == 1:
            # 单会话：原地标注聚合字段，避免多余 dict 拷贝（4450+ 条量级）
            r = sess[0]
            r["session_count"] = 1
            r["first_view_at"] = int(r.get("view_at") or 0)
            r["kids"] = [r.get("kid")]
            out.append(r)
            continue
        rep = dict(sess[-1])                       # 最近一次观看 = 展示基准
        progs = [x.get("progress") for x in sess]
        if any(p == -1 for p in progs):
            rep["progress"] = -1                   # 已看完优先（修 P0 误判）
        else:
            ints = [p for p in progs if isinstance(p, int)]
            rep["progress"] = max(ints) if ints else rep.get("progress")
        durs = [x.get("duration") or 0 for x in sess]
        if durs:
            rep["duration"] = max(durs)
        rep["session_count"] = len(sess)
        rep["first_view_at"] = int(sess[0].get("view_at") or 0)
        rep["kids"] = [x.get("kid") for x in sess]
        out.append(rep)
    return rest + out


# 阶段 2：Analyzer 可用性提示 —— 由 `server.probe_connection()`（唯一做网络 IO 的那层）刷新。
# `None` = 「尚未探测」→ 保持旧行为（照读 Analyzer 年表），避免启动早期就误跳过。
#
# ⚠️ 阶段 2 修正一（A2）：**结论必须会过期，且不能只凭"HTTP 不可达"就跳过读文件**
#    - 原先是一个「一次 False 就永久 False」的全局开关：Analyzer 重启后旧结论不失效，
#      auto 模式会**长期**跳过主源、停在本地快照上（而横幅另走 `/api/fetcher-health`，
#      显示的是"已连接"）→ 横幅说连着、数据却是旧的。
#    - 三条同时成立才允许跳过（`_analyzer_skippable()`）：近期探测过 + 结论不可达 + 主源文件不新。
#      第三条是关键 —— **读文件不需要服务在线**：迁移之后若 Analyzer 又写过主源
#      （mtime 更新），它就可能含本地库没有的记录/备注，此时必须照读。
_ANALYZER_PROBE = {"usable": None, "at": 0}
ANALYZER_PROBE_TTL = 300          # 秒；与前端 /api/fetcher-health 轮询同量级


def note_analyzer_usable(flag):
    """发布一次探测结论（server 层调用）。`None` = 撤销提示、回到「尚未探测」。

    带时间戳 —— 结论只在一小段时间内有效（见 `_analyzer_skippable()`）。
    """
    _ANALYZER_PROBE["usable"] = None if flag is None else bool(flag)
    _ANALYZER_PROBE["at"] = int(time.time())


def _analyzer_skippable():
    """auto 模式下能否安全跳过 Analyzer 年表读取 —— 三条**同时**成立才行：

    ① 最近一次探测结论是「不可达」；② 该结论未过期（`ANALYZER_PROBE_TTL`）；
    ③ 主源库文件不比本地库更新（否则它可能含本地库没有的记录）。
    任一条不成立都照读 —— **正确性优先于省一次 UNION**。

    注意：阶段 0 迁移后「本地库 ⊇ Analyzer 记录、合并结果不变」只是**迁移那一刻**的时点
    事实，不是不变量（运行期没有任何把 Analyzer 新数据回灌本地库的机制）—— 故本条不许
    被当作长期等价关系使用。
    """
    if _ANALYZER_PROBE.get("usable") is not False:
        return False
    if int(time.time()) - int(_ANALYZER_PROBE.get("at") or 0) > ANALYZER_PROBE_TTL:
        return False
    try:
        return os.path.getmtime(ANALYZER_DB) <= os.path.getmtime(LOCAL_DB)
    except OSError:
        return False               # mtime 读不到（文件缺失/无权限）→ 保守照读


def load_raw_records():
    """读取数据源并按「数据源主开关」合并为 canonical derived 列表。

    合并策略（mode=auto/analyzer）：以 kid 为键，**Analyzer 优先**；本地库补齐 Analyzer 没有的。
    合并策略（mode=local）：只用本地 Finder 库。
    auto 会在 Analyzer 读不到记录时**自动降级本地**，并把 effective 记入 _LAST_SOURCE 供前端横幅提示。
    返回 list[dict]，每项含规则引擎所需字段 + 展示字段 + 聚合字段（session_count/first_view_at/kids）。

    阶段 2：`mode=local`，或 `mode=auto` 且判定「主源这次读不读都一样」时
    （`_analyzer_skippable()`，判据见其上），直接跳过 Analyzer 年表读取 —— 这两种情况下
    它的结果本来就会被 `_pick_sources()` 丢弃。`mode=analyzer`（用户强制主源）不跳过。
    """
    mode = get_source_mode()                      # 只读一次：同一函数内两次读可能拿到不同值（A8）
    if mode == "local" or (mode == "auto" and _analyzer_skippable()):
        analyzer = {}
    else:
        analyzer = _read_analyzer(ANALYZER_DB)
    local = _read_local(LOCAL_DB)
    merged, effective = _pick_sources(mode, analyzer, local)
    _LAST_SOURCE.update({
        "requested": mode,
        "effective": effective,
        "analyzer": len(analyzer),
        "local": len(local),
        "merged": len(merged),
    })
    # Plan B：同一视频只显示最近一次观看，但保留「已看完」判断与全部会话键
    return _fold_latest_by_bvid(list(merged.values()))


# 模块级缓存：避免每请求重读 7 张年表
RAW_CACHE = {"records": None, "loaded_at": 0}


def reload_raw():
    RAW_CACHE["records"] = load_raw_records()
    RAW_CACHE["loaded_at"] = int(time.time())
    return RAW_CACHE["records"]


def get_raw():
    if RAW_CACHE["records"] is None:
        reload_raw()
    return RAW_CACHE["records"]


# ===================== 数据源主开关（#21，见 doc/archive/说明-主备架构与数据模式.md §3） =====================
# auto     ：Analyzer 有数据 → 以 Analyzer 为主源、本地库补齐；Analyzer 空/不可达 → 自动降级本地只读
# analyzer ：强制 Analyzer 为主源（本地仅补齐）
# local    ：强制只用本地 Finder 库（模式 B：自身抓取，需有效 SESSDATA）
SOURCE_MODES = ("auto", "analyzer", "local")
SOURCE = {"mode": "auto"}
# effective 初始为 None：表示「尚未加载过」，避免在 main() 的首次 reload 之前
# 对外谎报 effective=auto（前端据此显示"未加载"而非"Analyzer 主力"）。
_LAST_SOURCE = {"requested": "auto", "effective": None,
                "analyzer": 0, "local": 0, "merged": 0}


def set_source_mode(mode):
    """设置数据源模式（运行时生效，持久化由 server 层负责）。"""
    m = (mode or "").strip().lower()
    if m not in SOURCE_MODES:
        raise ValueError("mode 必须是 %s 之一" % (SOURCE_MODES,))
    SOURCE["mode"] = m
    return m


def get_source_mode():
    return SOURCE.get("mode") or "auto"


def _merge_records(analyzer, local, with_local_backfill=True):
    """Analyzer 优先，本地库仅补齐 Analyzer 没有的 kid（保持既有口径）。"""
    merged = dict(analyzer)
    if with_local_backfill:
        for kid, d in local.items():
            if kid not in merged:
                merged[kid] = d
    return merged


def _pick_sources(mode, analyzer, local):
    """按模式决定合并策略，返回 (records_dict, effective_mode)。

    auto 的降级判据用「Analyzer 是否读到记录」而非「库文件是否存在」——
    与 doc/archive/说明-主备架构与数据模式.md §6 的提醒一致：路径配错时不会假装有数据。
    """
    if mode == "local":
        return dict(local), "local"
    if mode == "analyzer":
        return _merge_records(analyzer, local, True), "analyzer"
    # auto
    if analyzer:
        return _merge_records(analyzer, local, True), "analyzer"
    return dict(local), "local"


def analyzer_db_diagnosis(path=None):
    """#37：区分「路径配错」与「Analyzer 确实没有数据」。

    两者在前端表现**完全相同**（都读不到记录 → auto 静默降级 local），但排查方向相反：
    前者要改 `data/source_config.json` 的 `analyzer_db`，后者要去让 Analyzer 跑一次抓取。
    全程只做 read-only 探测（`mode=ro`），不写任何文件。

    `path`（2026-10-01 新增，默认 None = 当前生效的 `ANALYZER_DB`）：
    允许对**候选路径**做同样的只读探测 —— `/api/data-source` 在**改动生效之前**先拿它
    验一遍（F-H2：旧实现不校验，坏路径照样落盘）。默认值不变，故既有调用点零影响。
    """
    p = path or ANALYZER_DB
    if not os.path.exists(p):
        return {
            "level": "error", "code": "path_missing",
            "message": "Analyzer 库路径不存在：%s —— 这属于「路径配错」，不是「Analyzer 没有数据」。"
                       "改 data/source_config.json 的 analyzer_db，或设环境变量 ANALYZER_DB。" % p,
        }
    if not os.path.isfile(p):
        return {"level": "error", "code": "not_a_file",
                "message": "Analyzer 库路径指向的不是文件：%s。" % p}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    except Exception as e:  # noqa
        return {"level": "error", "code": "unreadable",
                "message": "Analyzer 库打不开：%s（%s）" % (p, e)}
    try:
        cur = con.cursor()
        try:
            tables = _list_base_year_tables(cur)
        except Exception as e:  # noqa
            return {"level": "error", "code": "not_analyzer_db",
                    "message": "该文件不是 Analyzer 库（读不到年表）：%s。" % e}
        if not tables:
            return {"level": "error", "code": "no_year_tables",
                    "message": "该文件不是 Analyzer 库（没有 bilibili_history_YYYY 年表）：%s。" % p}
        total = 0
        for tb in tables:
            try:
                total += cur.execute('SELECT COUNT(*) FROM "%s"' % tb).fetchone()[0]
            except Exception:  # noqa
                pass
        if total == 0:
            return {"level": "warn", "code": "empty",
                    "message": "Analyzer 库可读，但 %d 张年表全为空 —— 先让 Analyzer 跑一次抓取。" % len(tables)}
        return {"level": "ok", "code": "ok",
                "message": "Analyzer 库正常：%d 张年表、%d 条记录。" % (len(tables), total)}
    finally:
        con.close()


def source_status(with_diagnosis=False):
    """当前数据源状态（供 /api/data-source 展示）。

    `with_diagnosis=False`（默认）时**完全不触库**，保持原有契约；
    为 True 时附带 `diagnosis` —— read-only 探测 Analyzer 库（见 `analyzer_db_diagnosis`）。
    """
    st = dict(_LAST_SOURCE)
    st["modes"] = list(SOURCE_MODES)
    st["analyzer_db"] = ANALYZER_DB
    st["analyzer_db_exists"] = os.path.exists(ANALYZER_DB)
    st["local_db"] = LOCAL_DB
    st["local_db_exists"] = os.path.exists(LOCAL_DB)
    if with_diagnosis:
        st["diagnosis"] = analyzer_db_diagnosis()
    return st


# ============ 「连接即模式」阶段 1：策略层 + 能力层 + 跨度读取（见 doc/archive/方案-连接即模式.md） ============
# 分工铁律（直接决定可测性）：
#   - 本段除 `load_policy` / `save_policy`（只读写一个 json）与 `*_span_days` / `local_meta`
#     （只读探测，`mode=ro`）之外，**全部是纯函数**：不碰 sqlite、不发 HTTP、不改任何源库。
#   - **唯一做网络 IO 的 `probe_connection()` 落在 server.py** —— 只有它持有 `_forward_fetcher`。
#   - 所以 `derive_capabilities` / `decide_sync_plan` 可脱离 HTTP 单测：喂一个假 probe 即可
#     （见 dev/test_capabilities.py）。
#
# 阶段 1 的承诺：**纯增、零行为变更** —— 旧端点（/api/fetcher-health、GET /api/data-source）
# 与旧按钮一行未动，本段暂无任何生产调用点（只有新端点 /api/capabilities 消费它）。

POLICY_FILE = os.path.join(DATA_DIR, "source_config.json")

# 策略默认值（D6）。`rules` 是**预留扩展位**：
# Q1 的"后续需要增加具体的策略" = 往这里加字段 + 往 R 表加一行，不需要改结构。
POLICY_DEFAULT = {
    "prefer": "auto",   # auto | local（Q1：界面上以「策略」名义呈现，不暴露内部词）
    "sync": "auto",     # auto | full | incremental（Q3：隐藏 override，不进 UI）
    "rules": {
        "full_cooldown_min": 10,    # R4 防连点：距上次全量的最小间隔（分钟）
        "full_interval_days": 7,    # R3 距上次全量的最小间隔（天）
        "gap_threshold_days": 30,   # R3 长尾缺口阈值（天）
    },
}

# 能力键（7 项）—— 冻结契约。相对 D2 的示意有 **1 处删减**：
#   `analysis` 已删除（后端无端点、前端无入口，且 D7 举例的 `.tab-analysis` 选择器实测不存在）；
#   原挂在它下面的 `#anRealtimeBtn`（「实时更新」）实为抓取入口 → 归入 `fetch`。
CAPABILITY_KEYS = ("fetch", "sync", "remark", "export", "images", "integrity", "backup")

# 能力语义（与前端 tooltip 共用同一份说法，避免两侧各写一份而漂移）
CAPABILITY_DOC = {
    "fetch": "从 B站拉取新历史记录（**写源库**）。组合形态走 Analyzer 中继，独立形态走本地 collector。",
    "sync": "让本地视图重读源库（**纯读**，不写任何源库）—— 本地永远可做。",
    "remark": "备注读写（写 Analyzer 主库的中继）。",
    "export": "导出（Excel / 整库 .db），均走 Analyzer 中继；本仓不自己生成 xlsx。",
    "images": "图片批量下载（Analyzer /images/* 中继）。封面自动缓存不在此列 —— 它直连 B站 CDN。",
    "integrity": "数据完整性自检（Analyzer /data_sync/* 中继）。",
    "backup": "本地备份快照（Finder 自己的动作，不依赖 Analyzer）。",
}


def normalize_policy(raw):
    """把任意配置 dict（含**旧的 `mode` 三态形式**）归一为 policy dict。纯函数。

    旧 → 新 映射（D1）：`local` → `prefer=local`；`auto` / `analyzer` → `prefer=auto`
    （`analyzer` 与"auto 且有数据"完全等价，是冗余分支，故删除）。
    缺字段一律回落 `POLICY_DEFAULT`，且 `rules` **递归补齐**（旧文件没有 rules 段）。
    """
    policy = {
        "prefer": POLICY_DEFAULT["prefer"],
        "sync": POLICY_DEFAULT["sync"],
        "rules": dict(POLICY_DEFAULT["rules"]),
    }
    raw = raw if isinstance(raw, dict) else {}
    pol = raw.get("policy") if isinstance(raw.get("policy"), dict) else None
    if pol:
        # 新格式优先；非法值静默回落默认（配置是人手改的，不该因一个错字而起不来）
        if pol.get("prefer") in ("auto", "local"):
            policy["prefer"] = pol["prefer"]
        if pol.get("sync") in ("auto", "full", "incremental"):
            policy["sync"] = pol["sync"]
        r = pol.get("rules") if isinstance(pol.get("rules"), dict) else {}
        for k, v in r.items():
            if k in policy["rules"] and not isinstance(v, (int, float)):
                continue          # 已知键必须是数字
            policy["rules"][k] = v
        return policy
    # 旧格式兼容（mode 三态 → prefer 二态）
    m = (raw.get("mode") or "").strip().lower()
    if m == "local":
        policy["prefer"] = "local"
    elif m in ("auto", "analyzer"):
        policy["prefer"] = "auto"
    return policy


def load_policy(path=None):
    """读取策略配置（**只读，绝不写盘**）。兼容旧 `{"mode": "..."}` 文件。"""
    p = path or POLICY_FILE
    d = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except Exception:
        d = {}
    return normalize_policy(d)


def _write_json_atomic(path, obj, where="store._write_json_atomic"):
    """原子写 JSON：同目录 `*.tmp` → `fsync` → `os.replace`。

    N-L3：配置类文件**不能**直写 —— 写到一半崩溃 / 断电会留下半截 JSON，
    下次读即解析失败，而读取侧又普遍"解析失败 → 静默回落默认值"，
    于是用户刚改的设置会**凭空消失且没有任何提示**。
    失败时**返回 False 并记一条失败**（不抛，保持既有调用方"不关心返回值"的写法可用）。
    """
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)          # 同目录替换 → 原子
        return True
    except Exception as e:
        note_failure(where, e, "path=%s" % path)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def save_policy(policy, path=None):
    """写回策略配置：**保留 `analyzer_db` 等其他字段**，并同步旧 `mode` 字段。

    同步 `mode` 的理由：阶段 1 不修改 `_load_source_config()`，它仍读 `mode` ——
    若只写 `policy` 段，旧读法会失效（这正是"删字段先于改消费方"的典型错序）。
    **阶段 1 没有任何调用点**（无调用点 = 无写盘风险），留待阶段 5 接上。

    N-L3（2026-10-01 修）：落盘改走 `_write_json_atomic`。本函数是 store 侧**唯一**
    写盘点，且当前无调用方 —— 属"休眠风险"，趁没有调用方时先换掉地基，
    阶段 5 真正接上时就无需再回头动这里。
    """
    p = path or POLICY_FILE
    cur = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            cur = json.load(f) or {}
    except FileNotFoundError:
        cur = {}                       # 首次运行 / 尚未生成：正常态，**不记失败**
    except Exception as e:
        note_failure("store.save_policy:read", e, "path=%s" % p)
        cur = {}
    if not isinstance(cur, dict):
        cur = {}
    norm = normalize_policy({"policy": policy if isinstance(policy, dict) else {}})
    cur["policy"] = norm
    cur["mode"] = "local" if norm["prefer"] == "local" else "auto"
    _write_json_atomic(p, cur, where="store.save_policy")
    return cur


def _rule_int(rules, key, default):
    """规则取整，容错（配置手改出错时回落默认，不让纯函数抛）。"""
    try:
        return int(rules.get(key, default))
    except Exception:
        return int(default)


def _cap(available, owner, reason, **extra):
    """能力项的统一结构：`{available, owner, reason}`（不可用时 owner 归 None）。"""
    d = {"available": bool(available),
         "owner": owner if available else None,
         "reason": "" if available else reason}
    d.update(extra)
    return d


_NEED_ANALYZER = "需要 Analyzer 连接（当前不可达）"


def derive_capabilities(probe):
    """能力层：**全项目唯一的可用性判断处**（纯函数）。

    取代此前散落的 12 处分叉（store 4 / server 5 / 前端 3）。前端只认
    `available` / `owner` / `reason` 三个键（外加 export 的 `formats`），不再自己判模式。
    """
    probe = probe if isinstance(probe, dict) else {}
    conn = probe.get("connection") if isinstance(probe.get("connection"), dict) else {}
    ana = conn.get("analyzer") if isinstance(conn.get("analyzer"), dict) else {}
    fin = conn.get("finder") if isinstance(conn.get("finder"), dict) else {}
    reachable = bool(ana.get("reachable"))
    # finder.sessdata 值域（阶段 1.5 ④ 扩展）：
    #   missing —— config.json 未填写
    #   present —— 已填写但**未联网验证**（周期路径恒为这个值，见 server._finder_sessdata_status）
    #   valid / invalid —— 真实探测（GET /api/finder-session-check）后的结论
    #   unknown —— 探测失败或结果不确定
    # 兼容性硬约束：`present` 与 `valid` **都视为可用**（不改变阶段 1 已验证的语义），
    # `invalid` 视为不可用并说明原因。
    fsess = fin.get("sessdata") or "unknown"

    caps = {}
    # fetch：写源库的抓取 —— 组合形态走 Analyzer 中继（凭证在 Analyzer）；
    # 独立形态走本地 collector，需要 Finder 自持凭证（与 R5 同一判据）。
    # 只有 `present` / `valid` 才给"可以抓"的结论；`missing` / `invalid` / `unknown` 一律保守 ——
    # 这一项决定"要不要让用户点一个会写源库的按钮"，探不出结论就不该让他点了再失败（D2）。
    if reachable:
        caps["fetch"] = _cap(True, "analyzer", "")
    elif fsess in ("present", "valid"):
        caps["fetch"] = _cap(True, "finder", "")
    elif fsess == "missing":
        caps["fetch"] = _cap(False, "finder", "需要有效 SESSDATA（config.json 未填写）")
    elif fsess == "invalid":
        caps["fetch"] = _cap(False, "finder", "SESSDATA 已失效（联网验证不通过），请更新 config.json")
    else:
        caps["fetch"] = _cap(False, "finder", "无法确认本地凭证（Finder SESSDATA 未知）")

    # sync：读源库的刷新（reload / apply）—— 纯本地动作，**恒可用**。
    caps["sync"] = _cap(True, "finder", "")

    # remark / export / images / integrity：全是 Analyzer-only 中继。
    caps["remark"] = _cap(reachable, "analyzer", _NEED_ANALYZER)
    caps["export"] = _cap(reachable, "analyzer", _NEED_ANALYZER,
                          formats=["excel", "db"] if reachable else [])
    caps["images"] = _cap(reachable, "analyzer", _NEED_ANALYZER)
    caps["integrity"] = _cap(reachable, "analyzer", _NEED_ANALYZER)

    # backup：Finder 自己的快照动作，不依赖 Analyzer。
    caps["backup"] = _cap(True, "finder", "")

    # 开发期护栏：键集合必须与 CAPABILITY_KEYS 一致，防止两侧漂移。
    assert tuple(caps.keys()) == CAPABILITY_KEYS, "capabilities 键与 CAPABILITY_KEYS 不一致"
    return caps


def span_advisory(span):
    """跨度差异的**说明性**结论（纯函数，**不参与任何决策**）。

    实测（2026-09-26）：主源 2452 天 / 本地 134 天。这个差异**既不是 bug 也不是"缺口"** ——
    它是 B站历史接口只保留近三个月这一硬约束的必然结果。本函数唯一的作用是把
    "要长尾就得做一次性迁移（P2）"这句话**在运行时直接告诉用户**，
    而不是让人误以为"多点几次同步就能补回来"。

    两种情形要分开（建议动作相反）：

    - 本地跨度 ≈ 90 天：**结构性**，属正常，无需动作（要更早长尾只能靠 P2 迁移）；
    - 本地跨度显著更短（< 60 天）：本地库疑似被清空/重建 → 建议重跑全量或从备份恢复。
    """
    span = span if isinstance(span, dict) else {}
    a, l = span.get("analyzer_days"), span.get("local_days")
    if a is None or l is None:
        return None
    a, l = int(a), int(l)
    gap = a - l
    if gap <= 90:
        return None
    if l >= 60:
        return {
            "kind": "structural_span_gap", "gap_days": gap,
            "analyzer_days": a, "local_days": l, "action": "none",
            "text": ("本地跨度 %d 天 < 主源 %d 天 —— 这是**结构性差异**（B站接口只保留近三个月），"
                     "属正常；全量抓取补不回更早的长尾，如需长尾请做一次性迁移（P2）。" % (l, a)),
        }
    return {
        "kind": "local_span_short", "gap_days": gap,
        "analyzer_days": a, "local_days": l, "action": "rebuild_local",
        "text": ("本地库仅覆盖 %d 天，疑似被清空或重建 —— 建议重跑一次全量，或从备份恢复"
                 "（P3 备份）。" % l),
    }


def _plan_owner(probe, policy):
    """本次抓取由谁执行（纯函数）：`prefer=local` 或 Analyzer 不可达 → finder。"""
    probe = probe if isinstance(probe, dict) else {}
    conn = probe.get("connection") if isinstance(probe.get("connection"), dict) else {}
    ana = conn.get("analyzer") if isinstance(conn.get("analyzer"), dict) else {}
    if isinstance(policy, dict) and policy.get("prefer") == "local":
        return "finder"
    return "analyzer" if ana.get("reachable") else "finder"


def decide_sync_plan(probe, policy, state):
    """策略层：**全项目唯一决定「全量 / 增量 / 跳过 / 阻断」的地方**（纯函数，Q3 落点）。

    规则表（D6，自上而下、命中即止）：

    | 规则 | 条件（owner = `_plan_owner()`）                          | 产出          |
    | --- | ----------------------------------------------------- | ----------- |
    | R5* | owner=finder **且** Finder 凭证不可用（missing / invalid / unknown） | `blocked`   |
    | 覆盖 | `policy.sync` 显式指定 full / incremental                  | 同名          |
    | R1  | 首次建基线（`last_success_at` 缺失 **或** 本地库为空）             | `full`      |
    | R2  | 上次增量报「未找到本地历史记录」（缺基线）**且不在全量冷却内**              | `full`      |
    | R3  | 本地跨度比**历史峰值**少 > `gap_threshold_days` **且** 距上次全量 > `full_interval_days` | `full`      |
    | R4  | 距上次全量 < `full_cooldown_min`                           | `skip`      |
    | R6  | 默认                                                    | `incremental` |

    > **对 D6 的两处实现说明（都是有意的）**
    > 1. **R5 提到规则表之前** —— 它是安全闸门，`sync` override 与 R1–R4 **都不应绕过它**
    >    （否则 `?full=1` 会在没凭证时发起一次必然失败的抓取）。
    > 2. **R5 的判据从 D6 的「`prefer=local` 且凭证无效」收紧为「`owner=finder` 且凭证不可用」** ——
    >    ① 这样"独立形态下没有凭证"也被挡住，而不只是显式选了 local 时；
    >    ② 它与 `derive_capabilities()` 的 `fetch` 项**共用同一判据**（阶段 1.5 ④ 后为
    >       `present` / `valid` 放行，`missing` / `invalid` / `unknown` 挡住），
    >       避免出现"能力说不能抓、策略说可以增量"的自相矛盾。
    >
    > R3 的判据是"**本地库丢过数据**"（当前跨度 vs 历史峰值），**不是**"主源比本地长多少" ——
    > 后者是 B站接口只保留近三个月造成的**结构性差异**，拿它当判据会让本条在任何状态下命中、
    > 每 `full_interval_days` 白跑一次全量（实测教训，见下面代码注释）。
    >
    > 3. **R2 与 R4 的判据共用**（阶段 2 修正 A1）—— 「缺基线」在冷却期内不再升级为全量，
    >    否则该标记一旦粘住，每次点击都会跑一次全量、且冷却形同不存在。

    返回 `{"mode": "full|incremental|skip|blocked", "reason": "...", "owner": "analyzer|finder"}`。
    """
    probe = probe if isinstance(probe, dict) else {}
    policy = normalize_policy({"policy": policy}) if policy else dict(POLICY_DEFAULT)
    state = state if isinstance(state, dict) else {}
    rules = policy.get("rules") or {}

    conn = probe.get("connection") if isinstance(probe.get("connection"), dict) else {}
    fin = conn.get("finder") if isinstance(conn.get("finder"), dict) else {}
    data = probe.get("data") if isinstance(probe.get("data"), dict) else {}
    span = data.get("span") if isinstance(data.get("span"), dict) else {}

    now = int(state.get("now") or time.time())
    owner = _plan_owner(probe, policy)
    fsess = fin.get("sessdata") or "unknown"
    full_cd = _rule_int(rules, "full_cooldown_min", 10) * 60
    full_iv = _rule_int(rules, "full_interval_days", 7) * 86400
    gap_th = _rule_int(rules, "gap_threshold_days", 30)

    # ---- R5（安全闸门，前置）----
    # 判据与 `derive_capabilities()` 的 fetch 项**完全一致**（一处判断、两处消费）——
    # 否则会出现"能力说不能抓、策略说可以增量"的自相矛盾。`present` / `valid` 放行
    # （阶段 1.5 ④：`valid` 是联网验证通过；`present` 是"已填写、未验证"的乐观结论）。
    if owner == "finder" and fsess not in ("present", "valid"):
        return {"mode": "blocked", "owner": "finder",
                "reason": "需要有效 SESSDATA（config.json %s，无法本地抓取）"
                          % ("未填写" if fsess == "missing"
                             else ("已验证失效" if fsess == "invalid" else "状态未知"))}

    # ---- 显式覆盖（policy.sync —— Q3 的隐藏 override，不进 UI）----
    ov = policy.get("sync")
    if ov == "full":
        return {"mode": "full", "owner": owner, "reason": "手动指定全量（隐藏 override）"}
    if ov == "incremental":
        return {"mode": "incremental", "owner": owner, "reason": "手动指定增量（隐藏 override）"}

    last_success = state.get("last_success_at")
    last_full = state.get("last_full_at")
    # 冷却判据**前置**计算：R2 也要用它收敛，见下（A1 的加重项 —— 原先 R2 排在 R4 之前，
    # 冷却对「缺基线」这条路完全不起作用）。
    cooling = last_full is not None and (now - int(last_full)) < full_cd

    # ---- R1 首次建基线 ----
    if not last_success or not int(state.get("local_count") or 0):
        return {"mode": "full", "owner": owner,
                "reason": "首次建基线（本地库为空或从未成功同步）"}

    # ---- R2 缺基线自动升级（= 现有 /api/fetcher-trigger 回退逻辑的判据）----
    if state.get("last_incremental_no_baseline"):
        # 收敛（A1）：冷却期内刚全量过 → 这次不必再全量（缺基线要么已被那次全量解决，
        # 要么那次全量的成功会清掉本标记，见 server._note_full_success）。
        if cooling:
            return {"mode": "skip", "owner": owner,
                    "reason": "缺基线，但 %d 秒前刚跑过全量，冷却中" % (now - int(last_full))}
        return {"mode": "full", "owner": owner, "reason": "缺基线，自动升级为全量"}

    # ---- R3 长尾缺口修复 ----
    # 判据 = **本地库"丢过数据"**：曾达到的跨度峰值明显大于当前跨度（`span_peak_days` 由 server
    # 从本地库 meta 读入，阶段 1 恒为 None → 本条不触发）。
    #
    # ⚠️ 为什么刻意**不用** `analyzer_days - local_days` 作判据（实测教训）：
    #    B站历史接口只保留近三个月，实测主源 2452 天 / 本地 134 天 —— 这是**结构性差异**
    #    而非"缺口"。若拿它当判据，本条会在**任何**状态下命中，导致每 `full_interval_days`
    #    就白跑一次全量（而全量同样补不回 2020–2024）。该差异改以 `advisory` 形式暴露给用户
    #    （见 `span_advisory()`），让它指向真正解法：一次性迁移（P2）。
    l_days = span.get("local_days")
    peak = state.get("span_peak_days")
    if l_days is not None and peak is not None:
        drop = int(peak) - int(l_days)
        stale = last_full is None or (now - int(last_full)) > full_iv
        if drop > gap_th and stale:
            return {"mode": "full", "owner": owner,
                    "reason": "修复长尾缺口（本地跨度比历史峰值少 %d 天）" % drop}

    # ---- R4 防连点 ----
    if cooling:
        return {"mode": "skip", "owner": owner,
                "reason": "刚刚跑过（%d 秒前），冷却中" % (now - int(last_full))}

    # ---- R6 默认 ----
    return {"mode": "incremental", "owner": owner, "reason": "常规增量"}


def _ro_conn(db_path):
    """只读连接（统一入口，避免各处重复拼 URI）。"""
    return sqlite3.connect("file:%s?mode=ro" % str(db_path).replace("\\", "/"), uri=True)


def _span_days(oldest, now):
    if not oldest:
        return None
    return max(0, int((int(now) - int(oldest)) / 86400))


def local_span_days(db_path=None, now=None):
    """本地库时间跨度（只读）：`{count, oldest, days}`；库不存在返回 count=0。

    `days` = 最早一条记录距今多少天 —— 它是 **R3「长尾缺口」的度量**（越长越完整）。
    """
    p = db_path or LOCAL_DB
    now = int(now if now is not None else time.time())
    out = {"count": 0, "oldest": None, "days": None}
    if not os.path.exists(p):
        return out
    try:
        con = _ro_conn(p)
        try:
            row = con.execute("SELECT COUNT(*), MIN(view_at) FROM history").fetchone()
        finally:
            con.close()
        out["count"] = int(row[0] or 0)
        out["oldest"] = int(row[1]) if row[1] else None
        out["days"] = _span_days(out["oldest"], now)
    except Exception as e:
        # C-M2（2026-10-01 复核修）：本函数是 **R3 判据的输入**，读失败若静默，
        # 表现与"本地库真的没跨度"完全一样（都是 days=None → R3 不触发）——
        # 正是 N-H1 要消灭的那类"看起来对、其实不对"。走 `_warn_fs_error`：
        # 库不存在已在上面 return（正常态），这里只剩"文件在、却读不动"。
        _warn_fs_error("store.local_span_days", e, p)
    return out


def analyzer_span_days(db_path=None, now=None):
    """Analyzer 主源时间跨度（只读，跨年表）：`{count, oldest, days, tables}`。"""
    p = db_path or ANALYZER_DB
    now = int(now if now is not None else time.time())
    out = {"count": 0, "oldest": None, "days": None, "tables": 0}
    if not os.path.exists(p):
        return out
    try:
        con = _ro_conn(p)
        try:
            cur = con.cursor()
            try:
                tables = _list_base_year_tables(cur)
            except Exception as e:
                _warn_fs_error("store.analyzer_span_days:list_tables", e, p)
                return out
            out["tables"] = len(tables)
            total, oldest = 0, None
            for tb in tables:
                try:
                    n, mn = cur.execute('SELECT COUNT(*), MIN(view_at) FROM "%s"' % tb).fetchone()
                except Exception as e:
                    # 单张年表读不动 → 跳过它，但**留痕**（否则"跨度偏短"会被当成事实）
                    _warn_fs_error("store.analyzer_span_days:table=%s" % tb, e, p)
                    continue
                total += int(n or 0)
                if mn and (oldest is None or int(mn) < oldest):
                    oldest = int(mn)
            out["count"] = total
            out["oldest"] = oldest
            out["days"] = _span_days(oldest, now)
        finally:
            con.close()
    except Exception as e:
        _warn_fs_error("store.analyzer_span_days", e, p)
    return out


def local_meta(keys, db_path=None):
    """读本地库 `meta` 表的若干键（只读）。缺失的键不出现在返回值里。"""
    p = db_path or LOCAL_DB
    out = {}
    if not os.path.exists(p):
        return out
    try:
        con = _ro_conn(p)
        try:
            for k in keys:
                row = con.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
                if row and row[0] is not None:
                    out[k] = row[0]
        finally:
            con.close()
    except Exception as e:
        # C-M2（2026-10-01 复核修）：`span_peak_days` 等键就靠这里读 ——
        # 读失败静默 = "没写过" 与 "读不到" 不可分。走 `_warn_fs_error` 留痕。
        _warn_fs_error("store.local_meta", e, p)
    return out


# ===================== 侧状态库（manual_skip / auto_exempt / meta） =====================

def _state_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    return sqlite3.connect(STATE_DB)


def _ensure_state_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS skip_state("
        "kid TEXT PRIMARY KEY, manual_skip INTEGER NOT NULL DEFAULT 0, "
        "auto_exempt INTEGER NOT NULL DEFAULT 0, "
        "archived INTEGER NOT NULL DEFAULT 0)"
    )
    # 迁移：旧库可能缺列（向前兼容）
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info('skip_state')").fetchall()}
        if "archived" not in cols:
            conn.execute("ALTER TABLE skip_state ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    conn.execute(
        "CREATE TABLE IF NOT EXISTS saved_views("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL, "
        "created_at INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS lists("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, "
        "values_json TEXT NOT NULL, created_at INTEGER NOT NULL)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")


def load_state():
    """读取侧状态：{kid: {manual_skip, auto_exempt, archived}}。"""
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        rows = conn.execute(
            "SELECT kid, manual_skip, auto_exempt, archived FROM skip_state"
        ).fetchall()
        return {kid: {"manual_skip": ms, "auto_exempt": ae, "archived": ar}
                for kid, ms, ae, ar in rows}
    finally:
        conn.close()


def save_skip(kid, manual_skip=None, auto_exempt=None, archived=None):
    """写入单条侧状态（manual_skip / auto_exempt / archived 可单独更新）。"""
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        cur = conn.execute(
            "SELECT manual_skip, auto_exempt, archived FROM skip_state WHERE kid=?", (kid,)
        ).fetchone()
        ms = cur[0] if cur else 0
        ae = cur[1] if cur else 0
        ar = cur[2] if cur else 0
        if manual_skip is not None:
            ms = 1 if manual_skip else 0
        if auto_exempt is not None:
            ae = 1 if auto_exempt else 0
        if archived is not None:
            ar = 1 if archived else 0
        conn.execute(
            "INSERT INTO skip_state(kid, manual_skip, auto_exempt, archived) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(kid) DO UPDATE SET manual_skip=excluded.manual_skip, "
            "auto_exempt=excluded.auto_exempt, archived=excluded.archived",
            (kid, ms, ae, ar),
        )
        conn.commit()
    finally:
        conn.close()


def delete_skip_states(kids):
    """删除侧状态库中指定 kid 的 `skip_state` 行（阶段 1.5 ③：删本地记录时同步清孤儿状态）。

    **只写自己的侧状态库**（data/canonical_state.db），不触碰任何源库 —— 与文件头
    「源库只读」的契约一致。返回实际清理行数；单事务、失败回滚。
    """
    kids = [str(k) for k in (kids or []) if k is not None and str(k).strip()]
    if not kids:
        return 0
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        n = 0
        for i in range(0, len(kids), 500):   # 分片：避开 sqlite 变量上限（默认 999）
            part = kids[i:i + 500]
            q = "DELETE FROM skip_state WHERE kid IN (%s)" % ",".join("?" * len(part))
            n += conn.execute(q, part).rowcount
        conn.commit()
        return n
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_meta(key, value):
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()


def get_meta(key, default=None):
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


# ===================== 分类（基于 engine，确定性，不写源库） =====================

def classify_record(rec, rule, now, state):
    """对单条 canonical 记录计算分类字段，返回带分类的输出 dict（不修改源）。

    在原语义基础上新增 archived 列（来自侧库 archived_state 表，批量归档落库）。
    """
    prog = rec.get("progress")
    finished = (prog == -1)
    kid = rec.get("kid")
    m = state.get(kid) or {}
    # Plan B 配套：折叠记录可能承载多个会话（kids），skip/archived **命中任一会话即生效**。
    # 否则「同一视频再看一次 → 产生新 view_at → 新 kid」会让既有 manual_skip 静默失效。
    for k in (rec.get("kids") or ()):
        if k == kid:
            continue
        mm = state.get(k)
        if not mm:
            continue
        if mm.get("manual_skip") or mm.get("auto_exempt") or mm.get("archived"):
            m = {
                "manual_skip": bool(m.get("manual_skip")) or bool(mm.get("manual_skip")),
                "auto_exempt": bool(m.get("auto_exempt")) or bool(mm.get("auto_exempt")),
                "archived": bool(m.get("archived")) or bool(mm.get("archived")),
            }
    manual_skip = bool(m.get("manual_skip"))
    auto_exempt = bool(m.get("auto_exempt"))
    archived = bool(m.get("archived"))
    auto_skip = 0
    reason = ""
    if not finished:
        res = engine.evaluate_rule(rec, rule, now)
        if res and not auto_exempt:
            action, label = res
            auto_skip = 1
            reason = f"{action}::{label}"
    if manual_skip:
        skip_state = "manual"
    elif auto_skip:
        skip_state = "auto"
    else:
        skip_state = ""
    if archived:
        skip_state = "archived" if not skip_state else skip_state
    out = {k: v for k, v in rec.items() if k != "source"}
    out.update({
        "finished": finished,
        "auto_skip": auto_skip,
        "auto_skip_reason": reason,
        "manual_skip": 1 if manual_skip else 0,
        "archived": 1 if archived else 0,
        "skip_state": skip_state,
    })
    return out


def _classify_all(raw_records, rule, now, state):
    return [classify_record(r, rule, now, state) for r in raw_records]


# ===================== 查询 / 视图 / 应用 / 状态 =====================

def _view_ok(r, view):
    """三视图 + 快捷视图筛选（保留分类核心语义）。"""
    if not view:
        return True
    if view == "all":
        return True
    if view == "finished":
        return bool(r["finished"])
    if view == "needs":
        if r["finished"]:
            return False
        if r["auto_skip"] != 0:
            return False
        # 与旧逻辑一致：≈看完（>=95%）视为不需要出现在「需要观看」
        dur = r.get("duration") or 0
        p = r.get("progress")
        if dur and p is not None and p >= 0 and p >= 0.95 * dur:
            return False
        return True
    if view == "skipped":
        return (r["auto_skip"] == 1 and
                r["auto_skip_reason"].startswith("auto_skip::"))
    if view == "stale":
        return (r["auto_skip"] == 1 and
                r["auto_skip_reason"].startswith("stale::"))
    if view == "archived":
        return bool(r.get("archived"))
    return True


def query(raw_records, rule, state, params):
    """统一查询入口：视图选择 + 高级筛选 spec + 全文搜索 + 排序 + 分页。

    params 支持：
      - view: 快捷视图 (all/finished/needs/skipped/stale/archived)
      - q: 全文搜索（标题/作者/UP主/bvid/kid）
      - filter: 高级筛选 spec（engine.evaluate_filter 所用 dict）
      - sort: [{field, dir}] 排序规范
      - limit / offset: 分页
    返回 (items, total, counts)。
    """
    now = int(time.time())
    classified = _classify_all(raw_records, rule, now, state)
    items, total = _filter(classified, params, now)
    counts = _view_counts(classified)
    return items, total, counts


def _filter(classified, params, now):
    view = (params.get("view") or "all").strip()
    q = (params.get("q") or "").strip()
    spec = params.get("filter")  # 高级筛选 spec dict（可空）
    sort_fields = params.get("sort") or []
    # 预构建 ctx：列表引用 + 聚合计数（供 having 用）
    lists = params.get("lists") or {}
    ctx_lists = {k: set(v) for k, v in lists.items()}
    group_counts = {}
    having = (spec or {}).get("having") if spec else None
    if having:
        gb = having.get("groupBy")
        if gb:
            from collections import Counter
            group_counts = dict(Counter(r.get(gb) for r in classified))
    ctx = {"lists": ctx_lists, "group_counts": group_counts}

    out = []
    for r in classified:
        if not _view_ok(r, view):
            continue
        if q:
            hay = " ".join(str(r.get(k) or "") for k in
                          ("title", "author_name", "bvid", "kid", "remark", "tag_name"))
            if q.lower() not in hay.lower():
                continue
        # 时间窗筛选（按 view_at 秒级时间戳）
        tf = params.get("time_from"); tt = params.get("time_to")
        if tf is not None or tt is not None:
            va = int(r.get("view_at") or 0)
            if tf is not None and va < int(tf):
                continue
            if tt is not None and va > int(tt):
                continue
        if spec:
            if not engine.evaluate_filter(r, spec, now, ctx):
                continue
        out.append(r)
    # 排序（稳定多列）
    if sort_fields:
        engine.apply_sort(out, sort_fields, now)
    else:
        out.sort(key=lambda x: x.get("view_at") or 0, reverse=True)
    total = len(out)
    limit = int(params.get("limit") or 60)
    offset = int(params.get("offset") or 0)
    return out[offset:offset + limit], total


def _view_counts(classified):
    finished = auto_skip = stale = needs = 0
    for r in classified:
        if r["finished"]:
            finished += 1
            continue
        if r["auto_skip"] == 1:
            if r["auto_skip_reason"].startswith("stale::"):
                stale += 1
            else:
                auto_skip += 1
        else:
            needs += 1
    return {
        "total": len(classified), "finished": finished,
        "auto_skip": auto_skip, "stale": stale, "needs": needs,
    }


def apply_rules(raw_records, rule, state, dry=False):
    """按当前 active 规则重扫：记录应用哈希，并清除被规则命中处的 manual_skip（auto 覆盖 manual）。

    分类是确定性计算的，apply 主要作用是固化「已应用」状态 + 清理冲突的 manual_skip。
    dry=True 仅计数不写库（供应用前确认）。
    """
    now = int(time.time())
    auto_set = stale_set = manual_cleared = 0
    for r in raw_records:
        kid = r.get("kid")
        if r.get("progress") == -1:
            continue
        res = engine.evaluate_rule(r, rule, now)
        if res:
            action, _ = res
            m = state.get(kid) or {}
            if m.get("manual_skip"):
                if not dry:
                    save_skip(kid, manual_skip=0)
                    state[kid] = {**m, "manual_skip": 0}
                manual_cleared += 1
            if action == "stale":
                stale_set += 1
            else:
                auto_set += 1
    return {
        "ok": True, "total": len(raw_records), "auto_set": auto_set,
        "stale_set": stale_set, "manual_cleared": manual_cleared, "dry": dry,
    }


def rules_status(raw_records, rule, state, rules):
    """返回规则应用状态：pending（待应用）+ dirty_count（自上次应用以来新增未套用）。"""
    current_hash = engine.rules_hash(rules)
    applied_hash = get_meta("rule_applied_hash", "")
    pending = bool(applied_hash) and applied_hash != current_hash
    last_apply_at = get_meta("last_rule_apply_at", "")
    now = int(time.time())
    dirty_count = 0
    if last_apply_at:
        la = int(last_apply_at)
        for r in raw_records:
            va = r.get("view_at") or 0
            if va > la and r.get("progress") != -1:
                m = state.get(r.get("kid")) or {}
                manual = bool(m.get("manual_skip"))
                if manual:
                    continue
                res = engine.evaluate_rule(r, rule, now)
                if res and not m.get("auto_exempt"):
                    continue  # 已被规则命中，不算脏
                dirty_count += 1
    return {
        "ok": True,
        "current_hash": current_hash,
        "applied_hash": applied_hash,
        "pending": pending,
        "dirty_count": dirty_count,
        "last_apply_at": int(last_apply_at) if last_apply_at else None,
    }


def get_meta_banner(raw_records):
    """常驻状态文字：主源 / 备份源 / 合并条数。"""
    analyzer_n = sum(1 for r in raw_records if r.get("source") == "analyzer")
    local_n = sum(1 for r in raw_records if r.get("source") == "local")
    return {
        "total": len(raw_records),
        "analyzer": analyzer_n,
        "local_backup": local_n,
        "analyzer_db": ANALYZER_DB if os.path.exists(ANALYZER_DB) else None,
    }


# ===================== 保存视图 / 黑白名单 / 批量操作 =====================

def save_view(view_id, name, spec):
    """保存一个筛选视图（覆盖式）。view_id 为空时自动生成。"""
    import uuid
    vid = view_id or ("view_" + uuid.uuid4().hex[:8])
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute(
            "INSERT INTO saved_views(id, name, spec, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, spec=excluded.spec",
            (vid, name, json.dumps(spec, ensure_ascii=False), int(time.time())),
        )
        conn.commit()
        return vid
    finally:
        conn.close()


def list_views():
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        rows = conn.execute(
            "SELECT id, name, spec, created_at FROM saved_views ORDER BY created_at DESC"
        ).fetchall()
        return [{"id": r[0], "name": r[1],
                 "spec": json.loads(r[2]), "created_at": r[3]} for r in rows]
    finally:
        conn.close()


def delete_view(view_id):
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute("DELETE FROM saved_views WHERE id=?", (view_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def save_list(list_id, name, kind, values):
    """保存黑白名单。kind='whitelist'|'blacklist'；values=list[str]。"""
    import uuid
    lid = list_id or ("list_" + uuid.uuid4().hex[:8])
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute(
            "INSERT INTO lists(id, name, kind, values_json, created_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "kind=excluded.kind, values_json=excluded.values_json",
            (lid, name, kind, json.dumps(values, ensure_ascii=False), int(time.time())),
        )
        conn.commit()
        return lid
    finally:
        conn.close()


def list_lists():
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        rows = conn.execute(
            "SELECT id, name, kind, values_json, created_at FROM lists ORDER BY created_at DESC"
        ).fetchall()
        return [{"id": r[0], "name": r[1], "kind": r[2],
                 "values": json.loads(r[3]), "created_at": r[4]} for r in rows]
    finally:
        conn.close()


def delete_list(list_id):
    conn = _state_conn()
    try:
        _ensure_state_schema(conn)
        conn.execute("DELETE FROM lists WHERE id=?", (list_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def batch_op(kids, action):
    """批量操作：archive(归档) / unarchive(取消归档) / skip(标记跳过) /
    unskip(取消跳过) / exempt(豁免自动跳过) / unexempt(取消豁免)。
    返回受影响的 kid 数。
    """
    affected = 0
    for kid in kids:
        if action == "archive":
            save_skip(kid, archived=1)
        elif action == "unarchive":
            save_skip(kid, archived=0)
        elif action == "skip":
            save_skip(kid, manual_skip=1)
        elif action == "unskip":
            save_skip(kid, manual_skip=0)
        elif action == "exempt":
            save_skip(kid, auto_exempt=1)
        elif action == "unexempt":
            save_skip(kid, auto_exempt=0)
        else:
            continue
        affected += 1
    return affected
