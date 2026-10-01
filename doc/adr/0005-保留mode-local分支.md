# 0005 · 保留 `mode=local` 分支

- 状态：已接受
- 日期：2026-09-26
- 背景：数据源主开关 `data_source.mode` 取 `auto / analyzer / local` 三态，其中 `local` 表示 Finder 用自己的库与凭证。既然 [0004](0004-保留collector.md) 保留了 `collector.py`，而 `local` 分支是它的唯一入口，去留需一并拍板（决策编号 **A3**）。
- 决策：**不做。保留 `mode=local` 分支。**
- 理由：它让"Analyzer 没装 / 没启动"时 Finder 仍可用，是降级路径的落地形态；两模式共用 `kid = (bvid, view_at)`，切换**不需要状态迁移**（见 [0001](0001-源库只读与侧状态库解耦.md)），保留成本低。
- 影响：`src/store.py` 的 `SOURCE_MODES` / `set_source_mode()` / `get_source_mode()` / `_pick_sources()` / `source_status()` 与前端「数据源模式」三选一**保持现状**；`auto` 的降级判据仍是「Analyzer 是否**读到记录**」而**不是**「库文件是否存在」（避免路径配错时"假装有数据"）。与 [0004](0004-保留collector.md) 成对。