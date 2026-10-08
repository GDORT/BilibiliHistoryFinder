# `dev/` —— 测试资产目录

> 状态：活跃
> 性质：导览
> 最后核对：2026-10-03 @ec54df8

> 位置：`BilibiliHistoryFinder/dev/` ｜ 整理：2026-09-26
> 定位：**开发期测试资产**。`src/` 下的运行时代码**不 import 这里**，整目录删掉也不影响服务运行。

## 一、资产清单

| 文件 | 干什么 | 跑法 | 边界 |
| --- | --- | --- | --- |
| `test_capabilities.py` | **「连接即模式」阶段 1 的纯函数单测**（仓库**唯一**的断言式单测）：`normalize_policy` / `derive_capabilities` / `decide_sync_plan` / `span_advisory` / `load_policy`·`save_policy` / 跨度读取 / `_analyzer_skippable`（跳过主源读取的三条前置）· `analyzer_gate_blocked`（阶段 3 门控的滞回 / 恢复不对称 / 绝不 fail-closed）· `CAP_DOM` 选择器护栏（阶段 4 `T2.8`：逐个验存在性，**T14 教训的自动化**）· `SOURCE_MODES` 二值域 ＋ 遗留值折算 ＋ `_pick_sources` 无 analyzer 分支（阶段 4 `T2.9`，**`#39` 护栏**）。**默认 289 项**；`--live` 追加一次 `/api/capabilities` 端到端冒烟（8765 在跑就打它，否则**进程内自起临时实例**，**共 320 项**），并对照旧端点行为不变 | `python dev/test_capabilities.py`<br>`python dev/test_capabilities.py --live` | 默认**零 IO**（只 import `store`）；临时 sqlite/json 全在 `tempfile.mkdtemp()` 里；`--live` 只发**只读 GET** 且带 `?sessdata=0`（连 B站都不打） |
| **`run_all.py`** | **统一 runner**（2026-10-06 新增，**「全绿」的单一权威出处**）：顺序跑 **8 套 × 两种环境**（带代理／无代理 —— 后者是 2026-10-06「假绿」事故的防线）＋ 汇总**退出码** ＋ **「文档声明 ↔ 实跑」交叉校验**（声明数与实跑不符即失败）＋ `data/` 零污染核对 ＋ 追加过程日志到 `test_runs.md`。**三道防线已负向验证**。⚠️ **A1 前端冒烟默认不开**，加 `--with-a1` → **9 套 / 累计 1454 次**（A1 也纳入声明回链与跨环境一致性） | `python dev/run_all.py`（`--env once` 只跑单环境；`--quiet` 只出汇总；`--no-declared` 跳过声明校验；**`--with-a1` 额外跑前端冒烟**） | **只调子脚本、自己零 IO**；默认跑两遍故耗时约 2 倍；结果写 `dev/test_runs.md`（追加式）。⚠️ **A1 走独立解释器**（`run_a1()` 解析 `dev/e2e/env.ps1` 取 `PYTHONPATH`／`PLAYWRIGHT_BROWSERS_PATH`，并从 `setup.ps1` 取解释器路径）→ **不必先 `. .env.ps1`** |
| **`e2e/smoke_frontend.py`** ⭐ | **前端运行时冒烟 · MVP-3**（2026-10-08，设计稿 §十）：**G1** 加载完整性 ＋ 夹具条数 · **G2** 能力→DOM 映射（置灰集合 == `available=false` ∩ `CAP_DOM` ＋ **独立基线**）· **G3** 横幅两档 · **G4** 同步四态 · **G5** 排序联动（**`#42` 行为锁**：升/降序缺值恒末尾，**已用回退实现验证过**）· **G6** `duration` 边界（**`#41` 行为锁**：`duration=0`/`NULL` 不被误标「短视频碎片」，**同样回退验证过**）。**54 项**：G1 3 · G2 9 · G3 9 · G4 11 · G5 11 · G6 7 ＋ 前置/环境 4（标签含「反例」者 **17 条**）。⭐ **设计稿 §六 的 6 组全部落地** | | `. dev\e2e\env.ps1` 后<br>`& "<setup.ps1 里的 python>" dev\e2e\smoke_frontend.py`<br>或直接 `python dev/run_all.py --with-a1` | 真起 `src/server.py` ＋ **真开 Chromium**；沙箱 `copytree src/` ＋ 空 `data/` ＋ 幽灵 Analyzer ＋ 随机端口（与 `verify_riskfix` 同构）；**零实网零真实数据**，跑完 `rmtree`。⚠️ **不断文案字符串**，只断类名/`hidden`/条数/`sig` 档位前缀（设计稿 §三 红线） |
| **`test_engine.py`** | **规则引擎单测**（2026-10-05 新增，**`engine.py` 498 行当时零覆盖**；现 542 行）：`_derived`·`eval_condition`（15+ 算子）·`eval_group`/`evaluate_rule`·**`classify`（五分类 ＋「finished＋auto_skip＋stale＋needs==total」不变量）**·`validate_rule` 冲突校验·规则装载/`rules_hash`·筛选器·`sort_value`/`apply_sort`。**114 项**。⚠️ **撞出 2 个真缺陷并当场修掉**（见 §6.5） | `python dev/test_engine.py` | **零 IO**（纯逻辑模块，不碰库/网络） |
| **`test_collector.py`** | **采集器离线部分单测**（2026-10-05 新增）：`classify_api_code`/`classify_http_status`（fatal/backoff/retry 三类）·`extract_fields`（含 `kid` 兜底构造）·`init_db`/`upsert`/`delete_records`（幂等 upsert、**库不存在不建库** A8 契约）·断点游标。**47 项** | `python dev/test_collector.py` | 只在 `tempfile.mkdtemp()` 临时库写，**绝不碰 `data/`**；不覆盖 `fetch_page`/`run`（那些要实网） |
| **`test_finder_collect.py`** | **Finder 自身抓取常驻回归**（2026-10-06 新增，闭合评估稿 **A2**）：沙箱 ＋ **假 B站**（`HTTPServer` 返构造的历史 JSON）＋ 死 Analyzer 端口逼策略派 `finder` → **真跑**「起 collector 子进程 → 拉历史 → 落库 → 写 `sync_result`」。**22 项**。⭐ 依赖 `src/collector.py` 新增的 **`BHF_API_BASE`**（**只为测试存在**，缺省仍是真 B站） | `python dev/test_finder_collect.py` | `copytree src/` 到 mkdtemp ＋ 空 `data/` ＋ **自建假 B站** ＋ 随机端口 ＋ 剥离代理变量；**零实网、零凭证**、可反复回归 |
| **`test_b2_live.py`** | **Finder 自身抓取真跑**（2026-10-05，B2）：沙箱 ＋ 真 SESSDATA ＋ 死 Analyzer 端口强制 `owner=finder` → **真发 B站请求**。⚠️ **会消耗风控额度**（实测 150s 拉 240 条） | `python dev/test_b2_live.py` | `copytree src/` 到 mkdtemp ＋ 空 `data/` ＋ **真 SESSDATA** ＋ 随机端口；**绝不碰真库**；结束即 rmtree |
| **`test_b2_sandbox.py`** | 同上的**无凭证版**：验证「无 SESSDATA 时策略正确 `blocked`」——**不花任何风控额度** | `python dev/test_b2_sandbox.py` | 同上（不含 SESSDATA） |
| **`backfill_duration.py`** | **`#40` 长尾 `duration` 补全**（2026-10-05，B3）：按 bvid 查 B站详情接口 `/x/web-interface/view?bvid=`。⭐ **该接口无需 SESSDATA** → 零风控额度。**363 条 → 成功 344（94.8%）**、19 条是「稿件不可见/已删」 | `python dev/backfill_duration.py` | **只读**（只查不写库）；产物 `data/duration_backfill.json` 由 `store` 叠加 |
| **`test_b3_dryrun.py`** | **补全前的 10 条探路**（2026-10-05，B3 前置）：随机抽 10 个 bvid 查详情接口，**先看风控反应与成功率再决定是否全量 363**。⭐ 实测 **10/10 成功、零风控** → 才敢跑全量 | `python dev/test_b3_dryrun.py` | **只读**（只查不写）；**真发 B站但仅 10 个请求** |
| **`_probe_sessdata.py`** | **两侧 SESSDATA 状态对比**（2026-10-05）：打印 Analyzer `_internal/config/config.yaml` 与 Finder `config.json` 各自的长度 ＋ sha12，并判定是否同步。⚠️ **踩过的坑**：Analyzer 配置在 **`_internal/config/`**（不是 `config/`）；Finder 的键是**大写 `SESSDATA`** | `python dev/_probe_sessdata.py` | **只读**（只打印，不改任何配置） |
| **`test_b4_dryrun.py`** | **规则全量重算预演**（2026-10-05，B4）：走 `store.apply_rules(dry=True)` ＋ 逐条 `evaluate_rule` 对比已落库值，**输出「会改哪些」而不写库**。⭐ **是 B4 真跑前的安全网** | `python dev/test_b4_dryrun.py` | **只读**（`dry=True`） |
| **`test_api_contract.py`** | **端点契约测试**（2026-10-05 新增）：`/api/capabilities`（七能力 ＋ 超集兼容 ＋ `mode`⟺`analyzer.ok`）·`/api/fetcher-health`（独有 `error`）·`/api/data-source`（**410 ＋ `moved_to`**）·`/api/sync`（GET 只读不触发）·`/api/query`（五分类 counts）·静态资源关键标志 ·`/api/backup`（**整机快照契约**）·**假 Analyzer 下的组合形态派活**。**97 项** | `python dev/test_api_contract.py` | `copytree src/` 到临时目录 ＋ 空 `data/` ＋ 幽灵库 ＋ 随机端口；**自带假 Analyzer**（`HTTPServer`）跑组合形态；跑完自动清理 |
| `regression_restart.py` | **⑥ 重启链路的常驻回归**。真起 `ThreadingHTTPServer` + 主线程 `serve_forever` + 工作线程发起重启（**生产拓扑**），连跑 3 次断言退出码均为哨兵 `42`；另含看门狗超时（`shutdown` 卡死 30s → 3s 顶出）、`_safe_print` 冻结对照、**旧设计对照**（`0/0/0`，线上 bug 的复现） | `python dev/regression_restart.py` | 只绑 `127.0.0.1:0`；写盘全部重定向临时目录 |
| `regression_fallback.py` | **② 自动回退（增量 → 全量）的端到端回归**。真起 `server.Handler` 发 HTTP GET；三条用例互相对照（缺基线**必须**回退 / 基线正常**不得**回退 / 其它错误**不得**回退）；**附加组**对 `POST /api/sync` 再断言一次（阶段 2 判据收敛）；**新增组（A1）**用**默认策略**跑两条 —— 缺基线＋冷却内 → `skip` 不发请求、原因落到 `sync_state["last"]`，缺基线＋已过冷却 → 全量成功后 `no_baseline` **必须被清**。**共 7 项** | `python dev/regression_fallback.py` | 同上；**base 走内存覆盖，`data/fetcher_config.json` 一字节不动** |
| `mock_analyzer.py` | **假 Analyzer**：增量接口固定回「未找到本地历史记录」以逼出回退分支；全量默认 `503`（→ Finder 判 `ok=false` → 不触发 `post`，**零副作用**）。`set BHF_MOCK_FULL=200` 可放行全量 | `python dev/mock_analyzer.py`（默认 `127.0.0.1:8790`） | 只绑 `127.0.0.1`；不连 B站、不读写真实历史库 |
| `stub_fetcher.py` | **假控制后端**（端口 `8899`）：真实 Analyzer 未开时，验证「实时更新」按钮 → 转发 → 刷新的**控制流闭合**。不真拉数据 | `python dev/stub_fetcher.py` | 只绑 `127.0.0.1:8899` |
| `verify_riskfix.py` | **风险审查修复的自动化验收**（2026-10-01）：第一批 `F-H1` 只绑回环 · `F-H3` 脏 body 返 400 · `F-H2` 坏路径被拒且不落盘 · `N-H1` 失败留痕（`FileNotFoundError` 不记）· `N-L3` 原子写；`log/2026-09-30-代码设计与风险审查.md` §8 收尾 `C-H1` body 上限 413 · `C-M1` 纯空白 400 · `C-H2` 同一配置文件的字段不被互相清掉 · `C-M2` span/meta 读失败留痕。**2026-10-02 追加 `A3` / `A5`**：主源为 Analyzer 时 `/api/local/delete` 必须 `blocked`（独立形态放行）· `/api/export/local/db` 返回有效 sqlite（magic 头）且临时快照不残留。**2026-10-03 追加阶段 3 门控组 `G-P`/`G-E`**：抗抖动滞回（连续 2 次才拦）· 恢复不对称（1 次成功即解封）· 记账过期放行 · 绝不 fail-closed；7 个 Analyzer-only 端点在假后端连发 502 时返 409 ＋ `capability`，且**门控在转发之前生效**（后端零请求）· 7 个本地/探测端点**不得误伤**（尤其 `/api/fetcher-health`）。假后端由脚本自建（`_FakeAnalyzer`，可切换 200/502），**结果不依赖本机 Analyzer 是否在跑**。**共 97 项断言** | `python dev/verify_riskfix.py` | 把 `src/` **整目录复制**到临时目录后**原样启动** `python <tmp>/src/server.py`（不 patch、不 mock `main()`）；`ANALYZER_DB` 指向沙箱内不存在的路径 → **不读真实 Analyzer 库**；用 `netstat` 独立核对监听地址；C-H1 用**裸 socket** 手搓「声称 5 MB、不发送 body」的请求（`urllib` 会按 data 长度自动填，捏不出来） |
| `migrate_p2.py` | **阶段 0 · P2 一次性数据迁移脚本** —— 把 Analyzer 主库的长尾 / 缺口导入本地库（**不是测试，是数据操作**；放这里是因为它只在开发期手动跑一次）。默认 `--dry-run` 预演，**`--apply` 才写** | `python dev/migrate_p2.py`<br>`python dev/migrate_p2.py --apply` | 会**写** `data/bilibili_history.db`（本地库）→ 跑前先备份；**不碰** Analyzer 主库（只读） |

### 关系图

```
regression_fallback.py ──复用 MockHandler──▶ mock_analyzer.py
        │                                          (独立假后端)
        │ 起 server.Handler（生产代码，非副本）
        ▼
  GET /api/fetcher-trigger ──转发──▶ http://127.0.0.1:<随机端口>
```

```
test_capabilities.py  （独立，不依赖任何假后端）
   ├─ 默认：import store ──▶ 纯函数（喂假 probe / 假 policy / 假 state）
   └─ --live：GET /api/capabilities ──▶ 8765（在跑）或 进程内自起的临时实例
```

```
verify_riskfix.py  —— **整进程级**验收（黑盒）
   ├─ 复制 src/ + 空 data/ 到临时目录（隔离副本，真实仓库零污染）
   ├─ subprocess：python <tmp>/src/server.py     ← 真实 main()、真实绑定循环
   ├─ HTTP：/api/data-source 脏 body / 坏路径 / 好路径
   └─ OS：netstat 逐条核对该端口只在回环监听
```

**两类测试的分工（阶段 1 起）**：

| | 纯函数单测（`test_capabilities.py`） | 集成回归（另三个） | 整进程验收（`verify_riskfix.py`） |
| --- | --- | --- | --- |
| 覆盖 | **决策逻辑**（能力表 / 策略表 / 配置归一） | **控制流与进程行为**（重启链、回退分支） | **启动期与边界行为**（绑定地址、脏输入、配置校验） |
| 成本 | 毫秒级、零 IO、可随时跑 | 秒级、起服务、要守安全约定 | 秒级、起**真进程** + 临时副本 |
| 何时用 | 改判据后**立刻**验证没改坏 | 改端点 / 线程 / 退出路径后 | 改启动流程 / 请求入口 / 配置读写后 |

> 阶段 2 要把抓取判据从「读手切模式」换成「读探测 + 策略」 —— 那时**先跑纯函数单测**（几秒内知道决策逻辑对不对），再跑集成回归。

`mock_analyzer.py` 单跑时是「手工版」（改配置 → 触发 → 还原，见 `doc/log/2026-10-01-待办-当前阶段.md` §6.2，「想亲眼看一次」时用）；
被 `regression_fallback.py` 复用时是「自动版」（内存覆盖，不改磁盘）。

## 二、四条共同安全约定

1. **绝不绑对外地址**：一律 `127.0.0.1` + 随机端口（`0`）。用完即 `shutdown()` + `server_close()`，不留后台进程。
2. **不碰历史数据**：不读写 `data/*.db` 与 Analyzer 主库；假后端不连 B站。
3. **写盘全部重定向临时目录**，且要**逐个覆盖**：
   `RUN_DIR` / `RESTARTS_FILE` / `RESTART_LOG` / `RELAUNCH_FLAG` 都是 `server.py` **模块顶层**用 `RUN_DIR` 拼死的常量——**改 `RUN_DIR` 不会连带改它们**。
4. **跑完核对零污染**：比对文件 md5 / `data/run/` 与 `data/backup/` 目录列表。

## 三、写新回归 / 单测脚本的七条经验（都是踩出来的）

1. **拓扑必须与生产同构**。要测一段写在 HTTP handler 里的逻辑（如 `/api/fetcher-trigger` 的回退分支），就**真起 handler 发真请求**，别把那段 `if` 抄进脚本——抄出来的是副本，线上照错。
   上任教训：`_verify_restart.py` 最初直接在主线程调 `_restart_after_response()`，天然拿到 42，**放过了真 bug**（生产里它在守护线程，退出码被吞成 0）。
2. **优先「内存覆盖」而不是「改磁盘配置」**。例：`_fetcher_cfg()` 的优先级是 `FETCHER_OVERRIDE`（模块级内存 dict）> 环境变量 > `data/fetcher_config.json` > 默认 → 测试时只改内存即可，**省掉「改配置 → 必须还原」整步**，风险从 🔵 降到 🟢。同理 `server.BACKUP_ROOT`、`server.RUN_DIR`。
3. **必须有反例对照**。只测「该触发时触发了」不够——一个永远返回 `true` 的实现也能通过。要同时测「**不该触发时不触发**」（如 `regression_fallback.py` 的用例 2 / 3）。
4. **副作用函数打桩而不是真跑**。唯一会真写盘的钩子（如 `_after_data_pull` → 真备份 + reload）替换成记录桩，被测的**分支判定逻辑本身保持真实**。
5. **⭐ 假样本测不出「键名漂移」——契约要用真实产出校验**。单测里自己构造的 dict 只能证明「算法对」；若生产代码改了一个字段名（而单测样本没跟着改），单测照样全绿。
   实例（2026-09-26 阶段 1）：`server.probe_connection()` 产出的 finder 段键名是 `state`，而 `store.derive_capabilities()` 读的是 `sessdata` → 独立形态下 `fetch` 恒被判「未知/不可用」；**7 个单测段全绿**，因为样本是我自己写的（两个名字都填了）。是 `--live` 那条「用**真实响应**再喂一次纯函数」的断言把它抓出来的。
   → 规则：**凡是有「两侧约定」的结构（probe ↔ 能力层、请求 ↔ 响应），必须有一条断言拿真实产出（或真实端点的响应）去驱动消费方。**
6. **⭐ 要测「启动期行为」，就把仓库复制一份再跑真进程**。绑定地址、`ensure_rules()`、`_load_source_config()`、`reload_all()` 这些**只在 `main()` 里跑一次**的东西，进程内 patch 测不到，而起真服务又必须动配置/裸奔监听。
   实例（2026-10-01 `verify_riskfix.py`）：把 `src/` **整目录** `copytree` 到临时目录、配一份**空 `data/`** 与 `config.json(web_port=空闲端口)`，再 `subprocess` 跑 `python <tmp>/src/server.py` —— `PROJECT_ROOT` 由 `__file__` 推导，于是**整个实例自然隔离**，一行生产代码都不用改。
   另外把 `ANALYZER_DB` 环境变量也指向沙箱内不存在的路径：**连「配置解析失败回落默认值」这条路也锁在沙箱里**（否则回落会读到真实 Analyzer 库）。
    配合 `netstat -ano` 做**独立于被测代码自称**的核对 —— 这是唯一能证伪「横幅说 127.0.0.1、实际绑 0.0.0.0」的手段。
7. **⭐ 断言里不能藏环境前提**——否则「环境变了」会被读成「代码坏了」。实例（2026-10-01）：`test_capabilities.py --live` 有一条「旧端点仍含 `status`」，而 `status` 是 **Analyzer `/health` 原样透传**的字段 → **Analyzer 一停（8899 不在跑），响应变成优雅降级体，这条就误报失败**；代码一行没坏，排查却要从「是不是我改坏了」绕一圈。
    → 规则：**断言只写「在所有可运行环境下都必须成立」的不变量**；随环境变化的分支要显式分流（如「可达 → 必须含 `status`；不可达 → 必须是降级体且不伪造 `status`」），并在**两种环境下都跑一次**再收工。
    → 推论：凡是「依赖另一个服务在跑」的断言，收尾时**要顺手验一遍对方不在跑的情形**（本次正是零成本验证：Analyzer 本来就没开）。

   > ⚠️ **第二次踩中同一类坑（2026-10-06）** —— 这次的**根因比「服务没开」更隐蔽**：
   > `test_api_contract.py` 的 P2 有三条断言（「含 `status`」＋「`sessdata.state=unknown`」＋「reason 说明未探测」），
   > 而它跑在 `start_sandbox()` 起的**幽灵 Analyzer**（`base_url` 写死 `http://127.0.0.1:1`）里 → **恒定红**。
   > **但我本机跑出来是「96 全绿」** —— 因为本机设了 `http_proxy`，`Popen` 不传 `env` 时子进程**继承代理变量**，
   > 请求 `127.0.0.1:1` 被代理接管回 **502** → 走 `_forward_fetcher` 的 `HTTPError` 分支（响应**含** `status`），
   > 而无代理时走 `URLError` 分支（**不含**）⇒ **同一份代码、两种结果**。
   > **两条修法（都已落地）**：
   > ① **沙箱 `Popen` 显式剥离代理变量** —— `env=` 只保留非代理项，让沙箱行为与「本机无代理」这一基准一致；
   > ② **断言按 `reachable` 分流** —— 不再无条件要求 `status`/`sessdata`，而是「可达 → 必须含 `status`；不可达 → 必须是降级体且**不伪造** `status`」。
   > **验收标准也改了**：这类脚本**必须在「带代理」与「无代理」两种环境下各跑一遍**才算绿（现已各跑一遍，均 96 全绿）。
   > ⚠️ **教训升级**：第 7 条说的「环境前提」不只是「**服务在不在**」，还包括「**进程继承了什么环境变量**」。
   > 断言的基准必须是**显式构造的沙箱**，而不是「恰好本机长什么样」。

## 四、本机注意事项

- **HTTP 代理会咬人**：命令行 `curl` 访问 `127.0.0.1` 会走代理并返回 **502**（看着像服务挂了）。→ curl 加 `--noproxy "*"`；Python 脚本在进程内临时 `os.environ.pop` 掉 `http_proxy / https_proxy / all_proxy`（大小写各一份）并设 `no_proxy=127.0.0.1,localhost`，否则 `urllib` 的转发也会被代理吃掉。**网页按钮不受影响。**
- **端口占用**：`8765` = 服务本体 ｜ `8899` = Analyzer ｜ `8790` = `mock_analyzer.py` 默认。
- **`pythonw.exe` 下 `sys.stdout / sys.stderr` 是 `None`**（GUI 子系统无控制台）→ 常驻脚本要兜底，否则静默死掉。

- **文档与文件约定**（改这个仓库的文档时踩过的坑，务必遵守）：
  - **表格单元格内不能有裸 `|`** —— 行内代码的反引号**挡不住** GFM 的列分隔符，整行表格结构会被破坏。→ 用 `·` 或转义 `\|`。
  - **换行必须跟文件一致，且改前先探测**（2026-10-01 重构后实测）：**CRLF** = `start.bat` / `stop.bat`（⚠️ **2026-10-06 更正**：原写「仓库根 `README.md`」是**错的** —— 实测仓库内存储与工作区**均为 LF**，故从名单移除） · `doc/archive/` 里 6 份历史遗留（`方案-后端与数据源.md` / `方案-前端.md` / `方案-Finder轻量化.md` / `说明-三方能力对照.md` / `说明-主备架构与数据模式.md` / `说明-同bvid多会话策略.md`）；**LF** = `doc/` 其余全部（4 份活跃 ＋ `adr/` ＋ `log/` ＋ 其余 `archive/`）· `src/*.py` · `dev/*`。给 CRLF 文件追加内容**不能直接用行编辑工具写 `\n`**（会混进裸 LF）→ 用脚本按 CRLF 拼接；改完用二进制读回核对「`CRLF>0` 且裸 `LF==0`」。
  - **探测换行必须用二进制模式读**（`open(p, 'rb')`）：Python **文本模式会把 `\r\n` 静默转成 `\n`**（universal newlines）→ 用文本模式检测换行**永远返回 LF**，再照此写回去，就把整个 CRLF 文件降级成 LF 了（2026-09-26 实际踩到，两份方案文档被转成 LF 后需手工恢复）。
  - **别拿 `git show` 判断工作区换行**：本仓库 `core.autocrlf=true`，仓库内存储恒为 LF，checkout 到工作区才变 CRLF → 判断格式一律**以工作区文件为准**。
  - **`.bat` 一律纯 ASCII + CRLF，且不要写 `chcp 65001`** —— 批处理中途切代码页是 cmd 的已知 bug，会吃掉后续行的首字符；中文 Windows 双击运行的 bat 若有中文，用 **GBK** 编码。
  - **大段中文别用 Bash heredoc 写 Python**（极易触发 `SyntaxError: Perhaps you forgot a comma`）→ 先用 Write 落成临时 `.md`，再用短脚本拼接。

## 五、跑法速查

```cmd
cd /d D:\Programs\Share\BilibiliHistoryFinder

rem ⭐ 日常跑这个：一次跑完 8 套 × 两种环境 ＋ 声明校验 ＋ 零污染核对
rem    退出码 0 = 全绿（本仓「全绿」的单一权威出处）
python dev\run_all.py
rem      --env once     只跑当前环境（快，但**不能作为验收依据**，见 §三 · 7）
rem      --quiet        只出汇总表
rem      --no-declared  跳过「文档声明 ↔ 实跑」校验
rem      --with-a1      额外跑 A1 前端运行时冒烟（起 Chromium）→ 9 套 / 1454 次断言
rem                      ⚠️ 需先装好隔离依赖：. dev\e2e\env.ps1（或重跑 dev\e2e\setup.ps1）
rem                      runner 会**自己解析 env.ps1**，不必先 . .（它同时从 setup.ps1 取解释器路径）

rem 启动 Finder（只起Finder；Analyzer 需另行双击，start.bat 不代启）
start.bat

rem 单跑某一套时：
python dev\test_capabilities.py           rem 阶段1 纯函数单测（零 IO，秒级）
python dev\test_capabilities.py --live    rem 追加 /api/capabilities 端到端冒烟
python dev\regression_restart.py          rem ⑥ 重启链路（42/42/42 + 旧设计 0/0/0 对照）
python dev\regression_fallback.py         rem ② 自动回退（7/7 + 零污染核对）
python dev\verify_riskfix.py              rem 风险审查修复验收（97 项，真进程 + 隔离副本）

rem 想亲眼看回退过程时（手工版，记得还原 base）：
python dev\mock_analyzer.py
curl --noproxy "*" -X POST -H "Content-Type: application/json" -d "{\"base\":\"http://127.0.0.1:8790\"}" http://127.0.0.1:8765/api/fetcher-config
curl --noproxy "*" http://127.0.0.1:8765/api/fetcher-trigger
curl --noproxy "*" -X POST -H "Content-Type: application/json" -d "{\"base\":\"http://localhost:8899\"}" http://127.0.0.1:8765/api/fetcher-config
```

## 六、测试规划与执行现状（原「待定」，2026-10-08 起已开工并落地）

> **状态：已落地执行（2026-10-08）。** 本节记录 **2026-10-01** 的评估结论与缺口清单，以及**这些缺口后来的闭合情况**。
> ⚠️ 原文写「待定 —— 尚未开工」，但截至 2026-10-08：**缺口 #3（前端零回归）已关闭**（A1 六组 **54 项**：G1 3 · G2 9 · G3 9 · G4 11 · G5 11 · G6 7 ＋ 前置 4），#1/#2/#4/#5 均已关闭；**`src/` 的测试代码一行未改**（全部在 `dev/`）。
> 评估对象：现有 `dev/` 七个脚本（六个测试 ＋ 一个数据迁移，见 §一）＋「连接即模式」后续阶段的可测性（阶段划分见 `doc/方案.md` §4）。

### 6.1 四层可测性（按「能否自动生成用例 + 自动验证」）

| 层 | 代表 | 自动生成用例 | 自动验证 | 现有手段 |
| --- | --- | --- | --- | --- |
| 纯逻辑层 | `store.derive_capabilities` / `decide_sync_plan`；**`engine.py` 续看规则引擎** | ✅ | ✅ 断言式 | `test_capabilities.py`（`engine.py` 尚无） |
| 请求 · 控制流层 | `server.Handler` 各端点、增量→全量回退分支 | ⚠️ 半自动（须起**真 handler**，不可抄 `if`） | ✅ | `regression_fallback.py` |
| 启动期 · 边界层 | 绑定地址、`ensure_rules()`、脏 body、配置读写 | ⚠️ 半自动（须**复制仓库跑真进程**） | ✅ | `verify_riskfix.py` |
| 前端层 | `web/index.html` / `web/app.js` 渲染与门控 | ✅ **A1 `smoke_frontend.py`**（2026-10-08，**54 项**：G1 3 · G2 9 · G3 9 · G4 11 · G5 11 · G6 7 ＋ 前置 4） | ✅ 含反例 ＋ **两个行为锁已回退验证**（`#41`/`#42`） | `dev/e2e/smoke_frontend.py` ＋ `make_fixtures.py`（需 `dev/e2e/` 隔离依赖） |
| 外部依赖 | B站接口 · 真实 Analyzer | ❌ 不可控 | ❌ | 以 `mock_analyzer.py` / `stub_fetcher.py` 顶替 |

**结论**：**前两层可高度自动化**（现有三个脚本已证明）；后两层只能「半自动」 —— 用例可写，但**必须真起进程 / 真发请求**，纯函数测不到。

### 6.2 「自动生成用例」的天花板（三类必须人定）

不论工具多强，下面三类**不能靠自动生成**，必须人工给定（对应 §三 的教训）：

1. **契约用例** —— 两侧约定的键名 / 形状（§三 · 5 的 `state` vs `sessdata`）。要么用真实产出驱动，要么人工写死。
2. **反例对照** —— 「**不该触发时不触发**」（§三 · 3）。只测正例的实现（永远返回 `true`）也能全绿。
3. **环境不变量** —— 「在所有可运行环境下都必须成立」的断言（§三 · 7）。随环境变化的分支要显式分流。

### 6.3 缺口清单（**2026-10-05 复核**）

| 优先 | 缺口 | 状态 | 说明 |
| --- | --- | --- | --- |
| ~~1~~ | ~~`engine.py` 零单测~~ | ✅ **已关闭**（2026-10-05） | 新增 **`dev/test_engine.py`**（**95 项断言**）覆盖 16 个函数 ＋ 五分类不变量。⚠️ **代价是撞出 2 个真缺陷**（见 §6.5） |
| ~~2~~ | ~~Finder 自身全量路径零覆盖~~ | ✅ **已关闭**（2026-10-06） | **自动化部分**由新增 **`dev/test_finder_collect.py`**（**22 项**，沙箱 ＋ 假 B站 ＋ 零实网）覆盖，不再只靠一次性 `test_b2_live.py`；「真发 B站」那一路仍保留为按需验证（耗风控额度） |
| ~~3~~ | ~~前端零回归~~ | ✅ **已关闭**（2026-10-08） | **`dev/e2e/smoke_frontend.py`**（**54 项**，**G1–G6 全组**）＋ **`dev/e2e/make_fixtures.py`**（39 条确定性夹具，§5 **前置 2 已闭合**）。真起 Chromium ＋ 真读 DOM；**`#41`/`#42` 两个行为锁均以「回退实现 → 断言必须变红」验证过**。⚠️ 维护成本：CAP_DOM 基线清单需随 **UI 结构改动**同步（⚠️ **阶段 6 已拍板不做**，不会再有这次改动） | 现状：阶段 4 已补**静态护栏**（`T2.8` 验 `CAP_DOM` 选择器存在性 · `T2.9` 验三态已删 ＋ 本次 `test_api_contract.P6` 验静态资源含关键标志），但**运行时交互（点击/渲染/排序联动）仍零覆盖**。真补需浏览器自动化（§6.4） |
| ~~4~~ | ~~无统一 runner~~ | ✅ **已关闭**（2026-10-06） | **`dev/run_all.py`**：顺序跑 **8 套 × 两种环境**（带代理／无代理）＋ 汇总退出码 ＋ **「文档声明 ↔ 实跑」交叉校验** ＋ 零污染核对 ＋ 追加过程日志到 `dev/test_runs.md`。**三道防线均已负向验证**（注入断言失败／改声明数字 → 退出码 1 并精准定位到具体项）。⚠️ 设计与执行落差（评估稿 §八）由此缓解 |
| **5** | **端点契约面** | ✅ **已关闭**（2026-10-05） | 新增 **`dev/test_api_contract.py`**（**97 项**）—— 此前只有 `--live` 验一个端点，现覆盖 7 个端点 ＋ 静态资源 ＋ 备份契约 ＋ 组合形态派活 |

### 6.4 浏览器冒烟需要什么（**A1 的代价评估** · 2026-10-06 复核）

前端要自动冒烟，**前置 5 项**。**当前缺 2 项**（原记 3 项，2026-10-06 复核后修正）：

> ⚠️ **本节状态更新（2026-10-09）**：下列「现状」栏是 **2026-10-06 的评估快照**。**前置 2 已于 2026-10-08 闭合** —— `dev/e2e/make_fixtures.py` 生成 39 条确定性夹具（五分类 ＋ `duration` 长尾）⇒ **A1（`dev/e2e/smoke_frontend.py`，54 项）已建成**，由 `run_all.py --with-a1` 调度。故下文「仍缺 / 等阶段 6 再建」等表述**已过时**；且 **阶段 6 经拍板不做**（`doc/待办.md`），前端不会再有那一次改动。

| # | 前置 | 现状（2026-10-06 实测） |
| --- | --- | --- |
| 1 | 可访问页面 / 隔离副本 | ✅ 已有 —— `verify_riskfix.py` 的 `copytree` ＋ 真进程做法可直接复用 |
| 2 | **种子夹具库（确定性数据）** | ❌ **仍缺 —— 最关键**。没有固定夹具，「看到几条」每次都不同，断言无从写起。**需造**：`data/bilibili_history.db`（固定 N 条，覆盖 finished/auto_skip/stale/needs 五类）× `canonical_state.db`（固定 skip 状态） |
| 3 | 可编程驱动 | ✅ **已就位**（2026-10-07）—— `dev/e2e/setup.ps1` 已装 Playwright（`pkgs/`）＋ Chromium 1243（`browsers/`），隔离不进全局；**已实测能启动浏览器并截图**。备选：本机 Edge 已装（`C://Program Files (x86)\Microsoft\Edge\Application\msedge.exe`）→ **可走 CDP 免下载浏览器**；若要 Playwright 需 `pip install` + `playwright install`（**约 +150 MB**） |
| 4 | 假 Analyzer 开关 | ✅ 已有 `mock_analyzer.py` ＋ 新增的 `BHF_API_BASE`（假 B站） |
| 5 | 代理绕过 | ✅ 已有约定（§四）＋ runner 已固化 |

**代价估算（诚实版）**：

| 项 | 代价 |
| --- | --- |
| **夹具库**（前置 2） | **主要成本** —— 造一份「五分类齐全 + 条数固定」的库，约 60–100 行数据生成脚本；还要**处理 `duration` 为 0 的长尾**（否则 `#41` 相关断言不稳） |
| **驱动**（前置 3） | 用现成 Edge 走 CDP：**零下载**、约 30 行连接代码；用 Playwright：**＋150 MB** 依赖 |
| **首批可自动化的断言** | 约 5–8 条**够用**的：页面加载无 JS 报错 · 横幅两档按形态切换 · 置灰数量与 `CAP_DOM` 一致 · 点击「同步数据」后 toast 显示 `engine` · 排序联动（升/降序缺值恒末尾，这是 `#42` 的行为锁） |
| **耗时** | 前置齐备后，写首批冒烟约 **2–3 小时**；连夹具带驱动约 **半天** |
| **长期维护** | ⚠️ **真实成本**：前端一改就可能挂断言。收益是**守住 `#41`/`#42` 这类「交互层才暴露」的回归** |

> **判断**：**值得做，但不必现在做**。理由：① 前端**运行时**回归确实只此一条路；② 但当前前端只有 1 个入口脚本 ＋ 纯原生 JS（无框架、无构建），交互层风险不高；③ ~~阶段 6 还没做（`UNAVAILABLE_MODE` 改 `hide`）——那时前端还会动一次，建议等阶段 6 定稿后再建夹具~~ —— **（2026-10-09 注：此条已过时）A1 已于 2026-10-08 建成，阶段 6 亦经拍板不做；实物见 `dev/e2e/smoke_frontend.py`。**
> ⭐ **零成本的替代（现在就能做）**：`dev/test_capabilities.py` 的 `T2.8` ＋ `test_api_contract.P6` 已覆盖**静态**契约（`CAP_DOM` 选择器存在性 · 关键标志在静态资源里）。**缺的是「点了会怎样」**，那部分只能靠 A1。

> 📐 **详细测试设计已补**（2026-10-07）：本节回答「要花多少」；「**怎么设计、为了什么**」见
> `doc/log/2026-10-07-设计-前端运行时自动化测试.md` —— 含目的（锁 `#41`/`#42` 等**交互层才暴露**的回归）
> · 边界 · 原则 · 三层架构 · **夹具设计**（闭合本节前置 2）· **用例清单**（6 组，各带反例）· 运行与
> `run_all.py` 集成 · 风险 · **判定标准** · MVP 三步。前置 3（驱动）已由 `dev/e2e/setup.ps1` 下载完成 ⇒ **A1 已从「待定」变为「可建」**。

### 6.5 测试方案分组（A 组沙箱可做 / B 组需授权） ＋ 撞出的缺陷

**为什么要分组**：本仓有些测试**本质上无法在沙箱内完成**（要真发 B站请求、要写真实数据），
把它们和常规回归混在一起会让人误以为「跑绿了就没事」。

| 组 | 范围 | 状态 |
| --- | --- | --- |
| **A 组 · 沙箱可独立完成** | 纯函数单测 ＋ 沙箱端到端（临时目录／随机端口／幽灵库／假 Analyzer），**零实网、零真实数据** | ✅ **已完成 3 项**：`test_engine` **114** ＋ `test_collector` **47** ＋ `test_api_contract` **97** ＝ **238 项** |
| **B 组 · 需用户授权** | ✅ **2026-10-05 全部执行完毕**（用户授权后逐项跑完）：① 真跑 `POST /api/backup` → `data/backup/20261005-140800/`（**三库各自独立** ＋ 6 配置文件 ＋ 9 条回代命令）② Finder 自身全量路径 → `dev/test_b2_live.py`（**真发 B站**，`engine=finder`，150s 拉 240 条 / 236 条带 duration）③ `#40` 补全 → `dev/backfill_duration.py`（**344/363 ＝ 94.8%**，详情接口**无需 SESSDATA** → 零风控）④ 规则全量重算 → `dev/test_b4_dryrun.py` 预演 ＋ 真打 `/api/apply-rules`（**与 dry-run 完全一致**）⑤ 真 Analyzer 联调 → `mode=combined`、`engine=analyzer` 派活正确 | ⚠️ **B1 首次执行时现场复现了「两个库 basename 相同 → Analyzer 快照被覆盖」的既存 bug**（当时跑的是旧进程）—— 已修并重跑；⛔ **B4 推翻了一个错误结论**：`store.apply_rules()` **不写** `auto_skip` 列（见 §6.5 下） |

#### ✅ A1 撞出的两个真缺陷（**2026-10-05 当场修掉**）

| # | 缺陷 | 修法 | 实测效果 |
| --- | --- | --- | --- |
| **`#41`** | `duration=0` 被规则误判为「短视频碎片」（`0 < 60` 成立） | **方案 B（用户拍板）**：`eval_condition` 加「**有值才判**」守卫 —— `field=="duration"` 且值 `None`/`≤0` 时**直接返 False**。⚠️ 守卫**刻意不拦** `exists`/`empty`；**有真实时长时行为完全不变** | **误伤 435 条 → 0 条**。462 条长尾里剩下 408 条命中的是**正确**的「陈旧」394 ＋「直播回放」14 |
| **`#42`** | 降序排序时缺值被排到最前（`(1,0)` ＋ `reverse=True`） | `apply_sort` 改为**分段**：有值段按方向排、**缺值段永远追加末尾**。⚠️ **刻意不动 `sort_value` 的返回形状**（避免波及其它调用方） | 升/降序缺值**都在末尾**；**稳定性已验**（同值保持原相对顺序） |

> **护栏已从「照实断言现状」改为「断言期望行为」** —— 修好后 95 项全绿。日后再改坏会**立即失败**。
> ⚠️ **B4（规则全量重算）现在安全了** —— 修前一旦执行，435 条长尾会被误标成「已跳过」并从「需要观看」消失；修后不会。

### 6.6 与 Analyzer 交互的接入点（参考 Frontend 模式）

`BiliHistoryFrontend-master` 的做法是**运行时可切换后端 base URL**（写 `localStorage.baseUrl` 后整页 reload）。本项目的**等价物**：

- 后端：`_fetcher_cfg()` 四级优先级（`FETCHER_OVERRIDE` 内存 dict > 环境变量 > `data/fetcher_config.json` > 默认）。
- 端点：`POST /api/fetcher-config` 改 base —— 测试一律走**内存覆盖**，不改磁盘（见 §三 · 2）。

> 注：`BiliHistoryFrontend-master` **自身零自动化测试**（`package.json` 无 `test` 脚本，无 vitest / jest / cypress / playwright）—— 它提供的只是「可切换 base」这一**联调模式**，没有测试代码可借鉴。
