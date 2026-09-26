# BilibiliHistoryFinder — B站历史查看器（原型版）

> **状态：原型阶段（Prototype）**。本仓库为「续看 / 历史筛选分类」规则的**原型实现**：用纯 Python 标准库把 B站观看历史抓到本地、长期留存，并用一个类 B站网页端的界面浏览、搜索、筛选，找出「还没看完」的视频。
> 后续正式版路线见 `doc/方案-后端与数据源.md`、`doc/方案-前端.md`、`doc/方案-油猴D.md`（按后端/前端/油猴三轴拆分，各带总览+阶段）；本 README 只描述**当前原型**的使用与结构。

---

## 1. 它能做什么

- **本地留存**：历史记录存进本地 SQLite，突破 B站官方「近 ~1000 条 / 3 个月滚动覆盖」的限制，从你开始用它的那天起一条不漏。
- **类网页端浏览**：封面 + 标题 + UP主 + 相对时间 + 进度条，可点击跳转 B站观看。
- **「需要观看」视图**：自动筛出"还没看完"（`progress < 95%` 或未完成）且未被规则跳过的视频。
- **续看规则引擎（auto_skip）**：按进度 / 时长 / 类型 / UP主等条件**批量标记"不需要观看"**，规则存于 `data/rules.json`，在 ⚙ 右侧抽屉中编辑。
- **三态跳过**：空 / 手动跳过 / 自动跳过，互斥且带角标；自动覆盖手动（最后操作优先）。
- **批量操作**：批量跳过 / 恢复 / 隐藏 / **归档**。
- **高级筛选器（规则引擎超集）**：组合布尔（AND/OR/NOR + NOT）、相对时间（近 N 天/超 N 天）、空值判断、多列排序、聚合 HAVING（同 UP 主> N 条）、黑白名单、保存视图预设——全部作为当前续看规则的读时查询层，不改变三视图归属。详见 `doc/方案-前端.md` §3.5（原 `记录筛选规则分析.md` §11 已并入）。
- **增量同步**：首次全量建基线，之后只拉新记录，快且不踩限流。

---

## 2. 快速开始（Windows）

### 2.1 关于登录态（SESSDATA）──当前有两份，职责不同
> 本仓库现阶段**读取的权威数据来自 Analyzer**（见 §4）。因此**主用凭证是 Analyzer 的 `config/config.yaml` 里的 SESSDATA**，**不是**本仓库根 `config.json`。

1. **主用（权威）：Analyzer 的 `config.yaml`**。若已装 BilibiliHistoryAnalyzer 后台，SESSDATA 填在这里（服务端读取，网页/前端都不碰凭证）。过期后在 Analyzer 侧重填即可。
2. **遗留（不推荐）：本仓库根 `config.json`**。仅供 `collector.py`（本地备份采集）使用，**当前该份已失效**（同步会报接口 `-101`）；按「计划 A」它将被停用（见 `doc/方案-后端与数据源.md` §7.4）。**新用户不必再填这份。**
   > **计划 A 已实现（2026-09-25，§7.11.2）**：数据源切换到 `auto`/`analyzer` 时，网页「同步数据」按钮**不再运行 `collector.py`**，改为转发 Analyzer 全量拉取——即 Finder 侧不再使用这份遗留凭证。**只有**把数据源模式显式切成 `local`（模式 B：自身抓取）时才需要它有效。
   > 凭证健康可在网页顶部横幅直接看到（🟢 正常 / 🔴 失效 / 🔴 Analyzer 不可达）；Analyzer 侧另有 `scheduler_config.yaml` 每 10 分钟检查并按邮件告警。

取值方式（两份通用）：
1. 用浏览器登录 B站（bilibili.com）。
2. 按 `F12` → **Application** → 左侧 **Cookies** → 选 `https://.bilibili.com` 域名。
3. 找到名为 **`SESSDATA`** 的条目，复制它的 **Value**（一长串字符）。

> ⚠️ SESSDATA 是登录凭证，泄露等于别人能登录你的账号，**请勿分享、勿提交到公开仓库**。它会过期（几天到几十天不等），过期后同步失败需重新取值。

### 2.2 启动（推荐用 bat）
仓库根目录已提供 `start.bat` / `stop.bat`：
- 双击 **`start.bat`**：自动探测本机 Python（兼容无系统级 Python 的情况）、**以 supervised 模式**前台启动服务并持续打印状态（含续看规则「待应用 / 脏计数」）。
  > "supervised"＝该窗口自己充当 supervisor：网页上点「⑥ 应用变更」触发重启时，服务会以退出码 42 退出，**窗口会自动把它重新拉起（≈3 秒）**，因此**改完代码无需关掉重开 bat**。
- 双击 **`stop.bat`**：关闭当前 8765 端口服务（正常停止，不会触发自动重启）。
- 浏览器打开 **http://127.0.0.1:8765** 即可使用。
- 改代码后如何生效 → 网页上**只需点一个按钮「⑥ 应用变更」**，不用你判断改了哪些文件：
  - 改了 `store.py` / `engine.py` / `rules.json` / 数据 → 自动**热重载**（约 0.1 s，不重启进程、不释放端口）；
  - 改了 `server.py` / `start.bat` → 自动**重启**（≈3 秒，由 `start.bat` 窗口拉起）；
  - 只改了 `src/web/*`（前端） → 自动提示**浏览器刷新**（Ctrl+F5），服务端零动作。
  按钮上的徽章 `● N` 会显示"有几处变更待应用"（需重启时转红）；它**只提示、绝不自动执行**。

### 2.3 手动启动（等价命令）
```bash
# 0) 数据来源：主源是 Analyzer（read-only 直连），本仓库不负责拉取；仅当无 Analyzer 时才用 collector 兜底。
#    （collector 为遗留路径，其 config.json 的 SESSDATA 当前已失效 → 计划 A 待停用，见 doc/方案-后端与数据源.md §7.4）
python src/collector.py --config config.json

# 启动 Web 服务（默认端口 8765）
python src/server.py

# 换端口：改 config.json 的 "web_port" 即可（端口只由配置文件决定，无环境变量开关）
```
> 注意：**手动 `python src/server.py` 启动时没有 supervisor**——点「⑥ 应用变更」若需要重启，接口会返回 `action: manual` 并**明确警告"重启后不会自动拉起"（且不会自杀）**，避免把服务点死。要用一键重启请走 `start.bat`。

---

## 3. 界面与核心操作

顶部工具栏分四组（类型 / 视图 / 浏览筛选 / 搜索 + 规则/批量/同步），右侧 ⚙ 抽屉默认隐藏：

- **类型 tab**（综合 / 视频 / 直播 / 专栏）：按内容类型筛选。
- **视图 tab**（全部 / 需要观看 / 已跳过 / 已搁置）：按"续看生命周期"切换。
- **筛选 ▾**：时长 / 时间 / 设备 / 含存档的**临时浏览条件**（不落库，刷新即还原）。
- **搜索**：按标题或 UP主名实时搜索。
- **⚙ 续看规则**：右侧抽屉编辑规则；橙色圆点＝待应用，数字＝脏计数（自上次应用以来未标记的新记录）。
- **同步数据 / 全量重建**：增量同步 / 强制全量校准"仅本地存档"标记。
  **计划 A 已实现（§7.11.2）**：数据源模式为 `auto`/`analyzer` 时**不再运行本仓库 collector**，改走 Analyzer 全量（中继）；仅 `local` 模式才用本地 collector。
- **Analyzer 联调（①–⑪）**：① 健康探测 ② 增量拉取 ③ 全量拉取 ④ 数据自检 ⑤ 本地备份 / 查看备份 ⑧ 导出 Excel ⑨ 下载整库 ⑩ 图片状态 ⑪ 下载图片 / 停止 —— 经本机 Analyzer（`:8899`）中继，详见 `doc/方案-前端.md` §6。
  - **⑧⑨ 导出**：转发 Analyzer `/export/*`，**Finder 自己不生成 Excel**（避免第二份口径）。
  - **⑩⑪ 图片**：转发 `/images/*`；封面/头像属公开内容，⑪ 默认 `use_sessdata=false`（**不消耗凭证**），且为**写盘操作**，会二次确认。
  - **卡片备注**：卡片上直接点备注处即可编辑，写回 **Analyzer 主库** `remark` 字段（与官方 Frontend 互通）。
- **数据源主开关（设置 ⚙ 内，§7.11.2）**：`auto`（默认：Analyzer 优先，读不到则降级本地）/ `analyzer`（强制主源）/ `local`（模式 B：只用本地库）。
  切换**即时生效并持久化**（`data/source_config.json`），无需重启；`kid=(bvid,view_at)` 跨模式一致 → **切换不丢跳过/视图/名单**。
- **数据源健康横幅（§7.11.2）**：Analyzer 不可达 / 凭证失效（-101）/ 处于自身模式或已降级时，页面顶部出现对应提示条；一切正常则隐藏。收起后同状态不再打扰。
- **⑥ 应用变更（唯一开发运维入口，不是数据功能）**：
  - **一个按钮，零判断**：`POST /api/apply` 自动比对"启动基线 vs 磁盘"，自己决定 → 热重载 / 重启 / 只需刷新 / 无需动作。
  - 徽章 `● N` = 待应用变更数（页面加载、窗口获得焦点、每 15 s 轮询 `GET /api/code-status`；`document.hidden` 时不请求）；**只提示、不自动执行**，建议动作为重启时徽章转红。
  - 需重启时前端会轮询 `boot_id` 直到**新进程**出现（避免旧进程在重启窗口内"假在线"）再自动刷新页面。
  - 防重启风暴：120 s 内重启 ≥3 次会被拒绝（返回 `blocked` + 剩余等待秒数）。
  - 手动 `python` 启动（无 supervisor）时若需重启，返回 `action: manual` 并**不自杀**，只尽力热重载可覆盖部分。
  - 底层端点 `POST /api/reload`、`POST /api/restart` 仍保留（API 层逃生口，`POST /api/apply?force=reload|restart` 可强制指定方式）。详见 `doc/方案-后端与数据源.md` §7.10。

### 续看规则引擎
- 规则存于 `data/rules.json`，同一时刻仅一条 `active` 生效；可在抽屉内增删改分组与条件。
- 每规则由若干**分组**组成，每分组含若干**条件**（字段 / 运算符 / 值），命中任一分组即按该分组动作处理：`auto_skip`（自动跳过）/ `stale`（已搁置）。
- 可用字段：`progress_pct`、`progress_sec`、`duration`、`business`、`author_name`、`author_mid`、`title`、`archived_only`、`view_at_age_days`。
- 运算符：`>= <= > <`、`between`、`in`/`not_in`、`contains`、`==`。
- **保存配置**＝写 `rules.json` 但不重算；**应用规则**＝对全量记录重扫并写 `auto_skip`/`auto_skip_reason`，应用前弹窗显示将标记条数供确认。

---

## 4. 架构

```
BilibiliHistoryFinder/
├── src/
│   ├── server.py         # 本地 Web 服务（http.server）：/api/* 历史/规则/跳过/查询 + Analyzer 中继/自检/备份
│   ├── store.py          # 只读数据层：读 Analyzer(主) + 本地库(备份) 合并 → canonical；按 bvid 折叠
│   ├── engine.py         # 续看规则引擎（纯逻辑，无 I/O）
│   ├── collector.py      # 采集器（遗留/备用）：SESSDATA + cursor 分页落 SQLite；按需调用，计划 A 待停用
│   ├── adapter_analyzer.py
│   └── web/              # 前端单页：index.html / app.js / style.css（零框架、零 npm）
├── data/
│   ├── canonical_state.db    # 侧状态库（skip/视图/名单；唯一可写）
│   ├── bilibili_history.db   # 本地备份库（collector 写入；**非主源**）
│   ├── rules.json            # 续看规则
│   ├── config.json           # 遗留配置（含 collector 用 SESSDATA，当前已失效）
│   ├── backup/               # ⑤ 本地备份快照（POST /api/backup 生成）
│   └── covers/               # 封面图片缓存
├── start.bat / stop.bat  # 启动 / 关闭（自动探测 Python；start.bat 为 supervised 模式，可被网页「⑥ 应用变更」自动拉起）
└── doc/                  # 文档（方案三轴 + 归档评估 + Fetcher/Frontend 源码副本）
```

- **主数据源**：Analyzer SQLite（`D:\Program Files (x86)\BilibiliHistoryAnalyzer\output\bilibili_history.db`，read-only 直连）；本地 `data/bilibili_history.db` 仅为**备份源**。
- **采集**：`collector.py` 仅用 Python 标准库（`urllib.request` / `sqlite3` / `json` / `datetime` / `argparse`），**无第三方依赖**；当前仅作遗留兜底。
- **服务**：`server.py` 用标准库 `http.server` 起本地静态服务，**零依赖、零构建**。
- **前端**：原生 HTML/CSS/JS 单页，可完全离线运行。
- **数据通路**：主源 read-only 直读 Analyzer；`progress` 覆盖更新（同一 kid 二次观看进度变大时覆盖，而非简单跳过）。

---

## 5. 数据文件

| 文件 | 作用 |
|---|---|
| `…\BilibiliHistoryAnalyzer\output\bilibili_history.db` | **主数据源**（Analyzer 写入；本仓库 read-only 直读） |
| `data/canonical_state.db` | 侧状态库：跳过状态 / 视图 / 名单（本仓库唯一可写） |
| `data/bilibili_history.db` | 本地**备份库**（collector 写入；非主源） |
| `data/backup/<时间戳>/` | ⑤ 本地备份快照（`POST /api/backup` 生成） |
| `data/source_config.json` | **数据源主开关**（`mode` + `analyzer_db`；`POST /api/data-source` 写入，2026-09-25 新增） |
| `data/backup_policy.json` | **备份触发/保留策略**（`auto` / `delta_threshold` / `keep`，2026-09-25 新增；可手改） |
| `data/config.json` | 遗留配置，含 collector 用 `SESSDATA`（当前已失效） |
| `data/run/restarts.json` | ⑥ 重启护栏记账（120 秒窗口内 ≥3 次则拒绝，防重启风暴） |
| `data/run/restart.log` | **重启链路留痕**（每次启动/重启写 2–4 行；排障「点了 ⑥ 到底走没走通」就看它，2026-09-25 新增） |
| `data/run/relaunch.flag` | **重启握手标记**：进程退出前落下它，`start.bat` 只看它在不在就重新拉起（**不依赖退出码**）；启动时自动清除（2026-09-25 新增） |
| `data/rules.json` | 续看规则（由 ⚙ 抽屉编辑，不直接手改） |
| `data/sync_progress.json` / `sync_result.json` | 同步进度与完成判定 |
| `data/auto_skip_progress.json` | 规则应用进度（运行时产生） |
| `data/covers/` | 封面图片缓存 |

> 💡 **备份建议**：真正的主数据在 **Analyzer 库**（上表第 1 行）；本仓库的 `canonical_state.db`（跳过状态）与 `data/bilibili_history.db`（本地备份）也建议一并复制，或直接用网页「⑤ 本地备份」按钮快照到 `data/backup/`。`config.json` / Analyzer `config.yaml` 含凭证，备份时注意保密。

---

## 6. 已知限制（原型）

- **只能"从现在起"留存**：B站端已被上限顶掉的过去历史无法恢复（服务端限制，任何方案都救不回）；原型价值是"以后一条不漏"。
- 单账号；多账号为后续阶段。
- SESSDATA 过期需手动更新。**2026-09-25 已补前端提示**：顶部横幅会在 Analyzer 凭证失效（-101）时变红提示，Analyzer 不可达或在自身模式/已降级时变黄（见 §3）。当前仍存在**两份**凭证（Analyzer `config.yaml`＝主用、根 `config.json`＝collector 遗留且已失效）；**计划 A 已实现**——`auto`/`analyzer` 模式下「同步数据」改走 Analyzer 全量，不再使用遗留凭证（见 `doc/方案-后端与数据源.md` §7.11.2）。
- 一键**本地备份**已实现（「⑤ 本地备份」按钮：`POST /api/backup` 对 Analyzer 主源 + 本地库做 sqlite3 在线快照至 `data/backup/<时间戳>/`，详见 `doc/方案-后端与数据源.md` §7.5）。**导出为独立文件已接入**：⑧ 导出 Excel / ⑨ 下载整库 `.db`，全部**中继 Analyzer `/export/*`**（Finder 不自己生成文件，避免第二份口径；§7.11.3）。
- **自动备份触发**（2026-09-25）：不做定时备份（Analyzer/Frontend 也都没有），改为**「自上次备份以来 Analyzer 主源新增条数 ≥ 阈值」**时在拉取成功后自动备份一次，并按 `keep` 清理旧快照（§7.11.4）。阈值见 `data/backup_policy.json`，可用 `GET /api/backup-policy` 查看当前增量与是否达线。
- **⑥ 应用变更与控制台的关系（2026-09-25 已修）**：Windows 控制台被鼠标**选中**时处于 QuickEdit 状态，向它的**任何输出都会阻塞**；旧实现把重启提示 `print` 放在 `os._exit(42)` 之前，于是「光标停在控制台里 → 点了 ⑥ 没反应」。现已三层防护：服务端输出全部改 `_safe_print`（守护线程，不阻塞）、重启链只写 `data/run/restart.log`、**3 秒看门狗**保证一定按退出码 42 退出；`start.bat` 的 `:RESTART` 段也改为零控制台输出。回归脚本 `dev/regression_restart.py`（详见 `doc/方案-后端与数据源.md` §7.12）。**另：退出码本身也曾不可靠**——旧实现把 `os._exit(42)` 放在**守护线程**里，`shutdown()` 之后主线程先走完、解释器收尾把守护线程回收，退出码变成 **0**，于是 `start.bat` 落到 `pause`、服务停住。现已改为**主线程决定退出码** + **文件握手**（回归对照：旧设计 3/3 得 0、新设计 3/3 得 42），详见 §7.13。**2026-09-26 00:15 已在 QuickEdit 冻结场景现场验证**：控制台全程保持选中（写入被冻结）时点 ⑥，整链 **≈ 3 秒**完成自动重启并刷新（修复前同一链路 56 秒）。
- 设备类型（`dt`）仅为占位标签（B站未公开真实设备映射）。
- 同步内自动套用规则暂缓，当前以"应用规则"为统一重校准入口。

---

## 7. 文档索引

> **方案文档（`doc/` 根：三份方案，按后端/前端/油猴三轴拆分，各带总览+阶段；另有当前阶段待办清单 + 一份轻量化讨论输入）**：
> 早期评估稿、架构分析、规则分析、源码评估报告等已**吸收进这三份方案并归档至 `doc/archive/`**，不再单独维护。

| 文档 | 内容 |
|---|---|
| `doc/方案-后端与数据源.md` | 方案·后端轴：目标运行状态（Analyzer+Finder+网页三方并存）、数据源选型、Frontend/Fetcher 替代评估、架构现状、Phase1 验证、规则引擎核心、统一契约、控制 Analyzer 变更量、**联调四步 + 轻量备份实际落地（§7）**、网页可控重启机制（§7.9）、**单入口「⑥ 应用变更」+ 自动判定 + 护栏 + 指纹 + 资源实测（§7.10）** |
| `doc/方案-前端.md` | 方案·前端轴：现有网页能力、历史展示页、高级筛选器超集（已落地）、控制触发 UI 变更量、独立前端全量设计评估 |
| `doc/方案-油猴D.md` | 方案·油猴轴：最终轻量形态（Phase 2 规划，尚未动手）、IndexedDB 双存储、引擎 JS 移植 |
| `doc/待办-当前阶段.md` | 当前阶段待办清单（不含 Phase 2 油猴 D）；供逐项标注「怎么做 / 是否做」 |
| `doc/方案-Finder轻量化.md` | **讨论输入**（2026-09-26）：单 Finder（独立）与 Finder+Analyzer 组合在**同一功能面**上的逐项对比 —— 实现者、需补代码量、凭证归属、失效面、维护面，以及组合模式下 Finder 现存包袱的实读体量（可减总量 ≈650 行）。**尚未形成方案** |

**源码副本（只读参考，非文档）**：`doc/BilibiliHistoryFetcher-master/`（Analyzer/Fetcher 后端源码）、`doc/BiliHistoryFrontend-master/`（开源前端，Tauri/Vue3）。二者为整份源码副本，供评估与字段/接口核对用。

**开发工具（`dev/`，非文档）**：`regression_restart.py` —— **⑥ 重启链路的常驻回归**（2026-09-25 由 `_verify_restart.py` 更名，转为正式资产）。跑法 `python dev/regression_restart.py`。它只绑 `127.0.0.1` 随机端口，**不启动对外服务、不读写任何历史数据**，所有写盘重定向到临时目录；断言生产拓扑（真实 `ThreadingHTTPServer` + 主线程 `serve_forever` + 工作线程发起重启）连跑 3 次退出码均为哨兵 **42**，并以旧设计对照（**0/0/0**，即线上 bug 的复现）。`mock_analyzer.py` —— **② 自动回退的离线复现桩**（2026-09-26 新增，与前者同级）：假扮 Analyzer，增量接口固定回「未找到本地历史记录」以逼出回退分支，全量接口默认返回 `503`（→ Finder 侧判定 `ok=false`，不触发 `post`，故测试**零副作用**；`set BHF_MOCK_FULL=200` 可放行全量）。只绑 `127.0.0.1`（默认 8790），不连 B站、不读写任何真实历史库；跑法与还原步骤见 `doc/待办-当前阶段.md` §6.2。`regression_fallback.py` —— **② 自动回退（增量 → 全量）的端到端回归**（2026-09-26 新增，与前者同级）。跑法 `python dev/regression_fallback.py`。它**真起 `server.Handler` + 真发 HTTP 请求**（不复制判定逻辑，避免测到副本）；**base 走内存覆盖 `FETCHER_OVERRIDE`**，因此 `data/fetcher_config.json` 一字节不动——省掉手工测法「改配置 → 必须还原」整步。三条用例互相对照：缺基线**必须**回退、基线正常**不得**回退、其它错误**不得**回退；`_after_data_pull`（唯一会真写备份 + reload 的函数）以记录桩替换，跑完核对 config md5 / `data/run/` / `data/backup/` 零污染。`stub_fetcher.py` 为接口桩。四个脚本的**用途、跑法、共同安全约定与写新回归脚本的经验**见 **`dev/README.md`**（测试资产手册）。

**归档溯源（`doc/archive/`，仅作历史参考，内容已并入上述三方案）**：
- `方案定稿-前后端分离前-2026-08-24.md`（拆分前单文件定稿）
- `架构分析.md`、`记录筛选规则分析.md`、`adapter-验证报告.md`（工程架构/规则/验证，已吸收）
- `BiliHistoryFrontend替代评估.md`、`Fetcher后端源码评估.md`（源码评估，结论已并入后端轴 §2.3）
- `测试版评估文档.md`、`哔哩哔哩历史查看器-需求评估.md`、`原型方案.md`、`使用说明.md`（评估期/原型期原始文档）

---

*原型版 README · 适用版本：本地 Web 版（零第三方依赖，纯 Python 标准库）。*
