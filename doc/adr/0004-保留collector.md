# 0004 · 保留 `collector.py`（不做去留变更）

- 状态：已接受
- 日期：2026-09-26
- 背景：`archive/方案-后端与数据源.md` 曾提出**「计划 A：双凭证治理」** —— 停用 `collector.py` 直连、Finder 改纯只读、SESSDATA 只保留 Analyzer `config.yaml` 一处；理由是 `collector.py` 是"坏掉的重复实现"（其 `config.json` 凭证已失效 `-101`），且"干净架构 = 删掉 `collector.py`"。是否删除需要拍板（决策编号 **A2**）。
- 决策：**不做。保留 `collector.py`。**
- 理由：① 它写的是自有 Finder 库（**不是**源库），不违反只读原则（见 [0001](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0001-源库只读与侧状态库解耦.md)）；② 它是「Analyzer 缺席时的降级自持能力」的落地件，仍有定位；③ 删除属方案级改动，收益（少一份维护）不抵风险 —— 会让独立形态彻底失去抓取能力。代价是承认"失效凭证路径仍可被触发"这一已知遗留。
- 影响：`src/collector.py` 保持现状。**`archive/方案-后端与数据源.md` §7.4 / §7.6 的「计划 A 停用 collector」表述自此作废**（正是本 ADR 取代的"计划"）。风险审查稿 `F-L2` 保持"只登记不修" —— 若要推进，**必须先重开本决策**。与 [0005](file:///d:/Programs/Share/BilibiliHistoryFinder/doc/adr/0005-保留mode-local分支.md) 成对。