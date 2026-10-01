# ADR · 决策记录

> 状态：活跃
> 性质：真相
> 最后核对：2026-10-01 @9ca9669

**一条决策一页，永不修改。** 需要推翻时，在新 ADR 里写 `状态：已取代（被 NNNN）` 并回指旧页；**旧页只加一行状态**，正文不动。

规范定义见 [../README.md](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/README.md) §4；模板固定六段：

```
# NNNN · <一句话决策>
- 状态：已接受 | 已取代（被 NNNN）
- 日期：YYYY-MM-DD
- 背景：为什么必须做这个决定（2–3 句）
- 决策：一句话
- 理由：为什么不选另一条路
- 影响：谁要跟着改
```

**为什么要有这一层**：以后不必再去《待定项与流程影响》的 §9.1 / §9.2 考古"当初为什么这么定" —— 一条决策一个稳定编号，可被任何文档引用。

---

## 索引

| 编号 | 决策 | 状态 | 日期 |
| --- | --- | --- | --- |
| [0001](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0001-源库只读与侧状态库解耦.md) | 源库全程只读，分类状态只落独立的侧状态库 | 已接受 | 2026-08-24 |
| [0002](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0002-移除BHF_PORT环境变量.md) | 移除 `BHF_PORT` 环境变量，端口只由 `config.json` 决定 | 已接受 | 2026-09-25 |
| [0003](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0003-span_peak_days写回策略.md) | R3 的 `span_peak_days` 由同步成功后写回；在此之前 R3 恒不触发 | 已接受 | 2026-09-26 |
| [0004](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0004-保留collector.md) | 保留 `collector.py`（不做去留变更） | 已接受 | 2026-09-26 |
| [0005](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0005-保留mode-local分支.md) | 保留 `mode=local` 分支 | 已接受 | 2026-09-26 |
| [0006](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0006-暂不拆中继.md) | 暂不拆中继 —— 先做「连接即模式」，中继保留、由能力层门控 | 已接受 | 2026-09-26 |
| [0007](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0007-油猴D暂缓Phase2.md) | 油猴 D 暂缓（Phase 2），不在当前阶段实施 | 已接受 | 2026-08-24 |

> 编号一经分配**不复用、不回收**。新决策一律追加在末尾。
>
> **命名消歧**：0004 / 0005 / 0006 所记的 **A2 / A3 / Q4** 是"连接即模式"阶段表里的决策编号；`archive/方案-后端与数据源.md` §7.4 / §7.8 里另有**「计划 A」（双凭证治理）**与**「方案 A」（显示层折叠）**两个同名字符串 —— 三者**互不相关**，引用时必须写全称。
>
> **「不删代码」路线**：0004 / 0005 / 0006 三条同向，共同构成"**不删代码、靠能力层门控**"的选择 —— 即 Finder 保留 `collector.py` / `mode=local` 分支 / 中继代码，改由能力表在运行时决定用不用。