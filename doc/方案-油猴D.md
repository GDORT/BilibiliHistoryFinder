# 方案 · 油猴 D（最终轻量形态）— BilibiliHistoryFinder 续看分类器

> 阶段：方案定稿期 · 油猴轴（2026-08-24 从单文件 `正式版方案.md` 拆分而出）
> 本篇定位：油猴脚本 D 的最终产品形态、与原生历史页的集成、引擎 JS 移植、IndexedDB 双存储。
> 姊妹篇：`方案-后端与数据源.md`（数据源/引擎/Analyzer适配）、`方案-前端.md`（网页展示/筛选/触发 UI）。
> 溯源：`doc/archive/方案定稿-前后端分离前-2026-08-24.md`（拆分前单文件定稿）。
> **状态：本篇为下阶段（Phase 2）规划，当前（2026-08-24）尚未动手；不阻塞前后端轴的已落地工作。**

---

## 0. 总览（油猴轴一页结论）

| 项 | 结论 |
|---|---|
| **最终交付形态** | **方案 D：油猴脚本 / 轻量扩展**（原生历史页手动拉取 + 浏览器本地存储） |
| **定位** | 部署最省（装插件即用）、原生体验最佳（站内 overlay）；是 Phase 1 网页验证后的"轻量收口形态" |
| **与 Phase 1 关系** | Phase 1 网页（Python 服务 + 浏览器）已验证规则引擎与全量三视图；油猴 D 复用同一套 canonical 契约与引擎语义，把引擎移植成 JS 直接在浏览器跑 |
| **核心挑战** | ① 引擎 JS 移植（Python `engine.py` → JS）② IndexedDB 双存储（抓完整 cursor 作 raw 权威层）③ 原生历史页 overlay 注入 + SPA 重渲染跟随 |
| **开发顺序** | 地基（Phase 0）→ 先接 Analyzer 全量（Phase 1，已落地）→ **油猴 D（Phase 2，本篇）** → 维护（Phase 3） |

> 一句话：油猴 D 是最终轻量形态，复用 Phase 0/1 已验证的 canonical 契约与引擎语义，把"规则引擎 + 分类 UI"搬进浏览器原生历史页。

### 0.1 当前实现状态（油猴轴，2026-08-24）
| 能力 | 状态 |
|---|---|
| 油猴骨架（@match / fetch hook / MutationObserver） | 🔲 未动手（Phase 2 规划） |
| IndexedDB 双存储（raw + derived，抓完整 cursor） | 🔲 未动手 |
| 规则引擎 JS 移植（含高级筛选器超集） | 🔲 未动手（以 `engine.py` 为参考实现） |
| 原生历史页 overlay（角标/筛选栏/置顶隐藏） | 🔲 未动手 |
| 浮动规则抽屉 | 🔲 未动手 |
| 混合形态收口 / 被挤掉记录处理 | 🔲 未动手 |
| 可选增强（加载全部/导出） | 🔲 未动手 |

> 注：油猴轴全部能力尚未开始；其依赖的 canonical 契约与引擎语义已由 Phase 0/1 验证，移植风险低。

---

## 1. 为什么还要做油猴 D（与网页端的关系）

- **网页端（Phase 1）**：read-only 消费 Analyzer 产物，需本机跑 Python 服务 + Analyzer 后台；适合"全量深历史分析 + 控制触发"的重场景。
- **油猴 D（Phase 2）**：零后端、装插件即用、原生站内 overlay；适合"日常刷历史时随手分类/续看提醒"的轻场景。
- **两者共用资产**：canonical `derived` 字段集、规则引擎语义基准（`eval_condition`/`eval_group`/`evaluate_rule`/冲突校验/三视图/`dirty_count`）、`rules.json` schema。Phase 1 的 Python 引擎是 JS 移植的"已验证参考实现"。

---

## 2. 阶段编排（油猴轴）

### Phase 2 — 油猴 D 落地（最终产品形态，复用 Phase 0/1 资产）

1. **油猴骨架**：`@match https://www.bilibili.com/history*`、`@run-at document-start` 的 `fetch` hook；Tampermonkey / Violentmonkey 元数据块；`MutationObserver` 跟随 SPA 重渲染。
2. **双存储（IndexedDB）**：`raw` 仓库（完整 `cursor` JSON 原样，复合 key）+ `derived` 仓库（规范化字段）；**必须抓完整 cursor 响应**（实测 Finder 现版 `raw_json` 缺 `business`/`oid`/`bvid` 等，不能作为权威层）。
3. **规则引擎 JS 移植**：条件组 / 冲突校验 / 三视图 / `dirty_count`，输入为 canonical `derived`（与 Phase 1 同一语义基准）。
4. **原生历史页 overlay**：顶部筛选栏、每条分类角标、命中项隐藏 / 置顶。
5. **浮动规则抽屉**：规则管理（单活跃切换、新增空白 / 预设、二次调整、冲突提示）。
6. **混合形态收口**：原生 overlay 为主 + 浮动面板 / 独立视图为辅（管理规则 + 翻「本地独有」记录）。
7. **被挤掉记录处理**：本地独有（服务端已无）分组；「仍存于 B站 / 仅本地」标注；不回写服务端。
8. **可选增强**：①「加载全部（本页时间窗）」半自动加速按钮；② 轻量导出 JSON/CSV。

### Phase 3 — 长期维护
- 跟进 B站历史页（Vue SPA）DOM / 接口变动；Analyzer / Fetcher schema 变动时仅更新 adapter，引擎不变。
- 规则引擎作为「跨数据源资产」持续打磨（同一套语义基准服务 D / A / B）。

---

## 3. 关键技术决策（油猴专属）

### 3.1 IndexedDB 双存储
- `raw`：完整 `cursor` 响应 JSON，key = `(bvid|oid)@view_at`，永不丢字段、永不 FIFO 淘汰——保证迁移零缺失。
- `derived`：规则引擎所需规范化字段（同 canonical `derived`）。
- **必须抓取完整 cursor**：Finder 现版 `raw_json` 仅 23 键（缺 `business`/`oid`/`bvid`/`cid`/`epid`/`page`/`part`/`dt`/`main_category`/`remark`），**不是真·原始响应权威层**，油猴须自己抓全。

### 3.2 引擎 JS 移植语义基准
- 以 Phase 1 `engine.py`（498 行，已验证）为参考，移植 `eval_condition` / `eval_group` / `evaluate_rule` / `classify` / 冲突校验 C1-C3 / 三视图 / `dirty_count`。
- 输入统一为 canonical `derived`，输出统一为 `{view, skip_state, conflicts, dirty_count}`。
- 高级筛选器超集（组合布尔/相对时间/空值/正则/聚合/名单/排序）一并移植。

### 3.3 原生历史页集成
- `fetch` hook 拦截历史页接口，注入分类角标；`MutationObserver` 跟随 SPA 重渲染补标。
- overlay 不破坏原页交互，仅叠加筛选栏 + 角标 + 置顶/隐藏。

### 3.4 数据主权
- 分类状态只存浏览器本地（IndexedDB），**绝不回写 B站服务端**。
- 本地独有记录（服务端已删）标注"仅本地"，可导出但不回传。

---

## 4. 变更量提示（油猴轴，下阶段）

- 油猴 D 是**新工程级**工作：估计 ~1500+ 行 JS（引擎移植 + IndexedDB + overlay + 抽屉）。
- 因与前后端轴解耦（独立存储、独立部署），其变更不影响 `方案-后端与数据源.md` / `方案-前端.md` 已稳定的内容——这正是拆分三轴的价值：**后续插入油猴相关变更，只在本文评估，不污染前后端方案。**

---

*本篇由 `doc/archive/方案定稿-前后端分离前-2026-08-24.md` 拆分而出；油猴 D 为 Phase 2 规划，当前未动手，复用 Phase 0/1 已验证的 canonical 契约与引擎语义。*
