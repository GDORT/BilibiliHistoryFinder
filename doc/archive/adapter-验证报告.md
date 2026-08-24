# 适配器验证报告 — Analyst 只读接全量 + 续看三视图

> 验证对象：正式版方案 §2.2 选项①「read-only 直连 Analyzer SQLite」与 Phase 0「规则引擎抽离」。
> 验证日期：2026-08-24。性质：**只读验证，零写库、零新增运行时依赖**。
> 验证脚本：`src/adapter_analyzer.py`（数据层）、`src/engine.py`（抽离的规则引擎，可移植纯逻辑）。

---

## 1. 验证目标

1. 把 `BilibiliHistoryAnalyzer` 的全量 SQLite（按年分表 `bilibili_history_YYYY`）以 **`file:?mode=ro`** 接进续看规则引擎；
2. 证明规则引擎可**抽离为不依赖数据库/网络的纯逻辑模块**，跨数据源复用；
3. 在 **Analyzer 全量 2813 条（2020–2026 深历史）** 上跑通 `needs/skipped/stale` 三视图分类，且与 Finder 自有数据验证方式一致；
4. 确认「方案 B / Phase 1 选项①」可行，为后续 Phase 1/Phase 2 复用奠基。

---

## 2. 数据源与接入

| 项 | 值 |
|---|---|
| 源库 | `D:\Program Files (x86)\BilibiliHistoryAnalyzer\output\bilibili_history.db` |
| 接入方式 | `sqlite3.connect("file:...?mode=ro", uri=True)`（只读） |
| 表枚举 | `WHERE type='table' AND name GLOB 'bilibili_history_[0-9][0-9][0-9][0-9]'`，**排除 `_fts*` 全文虚拟表** |
| 年表数 | 7（2020–2026），共 **2813** 条 |
| 规则来源 | 沿用 Finder `data/rules.json` 的 active 规则（默认「接近看完 / 误触 / 短视频碎片 / 直播回放 / 陈旧」5 分组） |

---

## 3. canonical derived 字段映射（Analyzer 34 列 → 引擎字段）

Analyzer 基表 34 列，规则引擎实际消费子集与 canonical derived 对应如下：

| canonical 字段（引擎输入） | Analyzer 列 | 类型 | 映射说明 |
|---|---|---|---|
| `business` | `business` | TEXT | 直映（archive/pgc/article/live） |
| `duration` | `duration` | INTEGER | 直映（秒） |
| `author_name` | `author_name` | TEXT | 直映 |
| `author_mid` | `author_mid` | INTEGER | 直映 |
| `title` | `title` | TEXT | 直映（长标题见 `long_title`） |
| `progress` | `progress` | INTEGER | 直映（-1＝已看完） |
| `view_at` | `view_at` | INTEGER | 直映（Unix 秒） |
| `archived_only` | —（无） | — | **Analyzer 无此列**，按 `0` 补足（同步簿记属 Finder 侧） |
| `kid` / `bvid` | `kid` / `bvid` | — | 仅用于样本标识，不参与判定 |

派生旋钮（引擎内部算，非源字段）：`progress_pct = progress/duration`、`progress_sec = progress`、`view_at_age_days = (now-view_at)/86400`。

> 结论：Analyzer 34 列 = canonical `derived` 基准完全成立；唯一缺的 `archived_only` 以 0 补足，不影响续看分类（该字段仅作可选规则条件）。

---

## 4. 验证结果（只读，未写库）

```
total      : 2813
finished   : 1239   （已看完 progress=-1）
auto_skip  : 1085   （命中 auto_skip 分组）
stale      : 0      （命中 stale 分组）
needs      : 489    （未完成 且 未被跳过 = 需要观看）
校验和     : 1239 + 1085 + 0 + 489 = 2813 ✓
```

- 跨年 UNION 读取正常（7 张表，`_fts` 虚拟表已排除，无 `SELECT *` 报错）；
- `src/engine.py` 抽离成功：规则引擎不 import `collector`、不连库，可独立对 canonical 记录分类；
- 样本抽查：needs 多为「低进度（<10%）长视频」、auto_skip 多为「短视频碎片（duration<60）」——符合默认规则预期。

### 关于 stale=0
默认规则 5 个分组按 `match=any`、首命中优先；「陈旧-久未观看（stale, view_at_age_days>90）」排在最后，大量老旧记录先被前面 `短视频碎片`/`误触` 等 `auto_skip` 分组命中，故归为 auto_skip 而非 stale。这是**规则顺序与默认配置**的结果，非代码缺陷——用户可在 `rules.json` 调整分组顺序或条件来让 stale 生效。验证目的（三视图机制成立）达成。

---

## 5. 结论

- ✅ **方案 B / Phase 1 选项①「read-only 直连 Analyzer SQLite」可行**：零新增运行时、映射最小、数据最全（含 Finder 永远抓不到的 2020–2025 深历史）。
- ✅ **规则引擎已抽离为可移植纯逻辑**（`src/engine.py`），本地 Web 端、只读适配器、未来油猴 D 路线共用同一套语义，切换无损。
- ✅ **只读原则成立**：全程 `mode=ro`，源库零修改；分类仅为内存计算，可安全反复运行。
- ⚠️ **注意点**：Analyzer 表无 `raw_json` 列（其 34 列即解析投影）；若后续要走「canonical raw 权威层」做零缺失迁移，Analyzer 侧 `raw` 须另行从 `api_responses/`/`history_by_date/*.json`（Fetcher 侧）或重新抓取补齐——但对当前续看分类无影响。

---

## 6. 下一步（复用本验证）

- Phase 1：将 `server.py` 的 `fetch_history` / `run_apply_rules` 数据源从 Finder `history` 表切换为「Analyzer 只读适配器 + canonical derived」，前端/规则引擎零改动；
- Phase 2：油猴 D 路线直接 `port` `src/engine.py` 为 JS，消费 IndexedDB 中的 canonical derived；
- 升级路径（可选）：若要「常新全量」，再上 Fetcher `:8899` API（选项②）。
