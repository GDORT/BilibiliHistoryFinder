# `dev/` —— 测试资产目录

> 位置：`BilibiliHistoryFinder/dev/` ｜ 整理：2026-09-26
> 定位：**开发期测试资产**。`src/` 下的运行时代码**不 import 这里**，整目录删掉也不影响服务运行。

## 一、资产清单

| 文件 | 干什么 | 跑法 | 边界 |
| --- | --- | --- | --- |
| `test_capabilities.py` | **「连接即模式」阶段 1 的纯函数单测**（仓库**唯一**的断言式单测）：`normalize_policy` / `derive_capabilities` / `decide_sync_plan` / `span_advisory` / `load_policy`·`save_policy` / 跨度读取。`--live` 追加一次 `/api/capabilities` 端到端冒烟（8765 在跑就打它，否则**进程内自起临时实例**），并对照旧端点行为不变 | `python dev/test_capabilities.py`<br>`python dev/test_capabilities.py --live` | 默认**零 IO**（只 import `store`）；临时 sqlite/json 全在 `tempfile.mkdtemp()` 里；`--live` 只发**只读 GET** 且带 `?sessdata=0`（连 B站都不打） |
| `regression_restart.py` | **⑥ 重启链路的常驻回归**。真起 `ThreadingHTTPServer` + 主线程 `serve_forever` + 工作线程发起重启（**生产拓扑**），连跑 3 次断言退出码均为哨兵 `42`；另含看门狗超时（`shutdown` 卡死 30s → 3s 顶出）、`_safe_print` 冻结对照、**旧设计对照**（`0/0/0`，线上 bug 的复现） | `python dev/regression_restart.py` | 只绑 `127.0.0.1:0`；写盘全部重定向临时目录 |
| `regression_fallback.py` | **② 自动回退（增量 → 全量）的端到端回归**。真起 `server.Handler` 发 HTTP GET；三条用例互相对照（缺基线**必须**回退 / 基线正常**不得**回退 / 其它错误**不得**回退） | `python dev/regression_fallback.py` | 同上；**base 走内存覆盖，`data/fetcher_config.json` 一字节不动** |
| `mock_analyzer.py` | **假 Analyzer**：增量接口固定回「未找到本地历史记录」以逼出回退分支；全量默认 `503`（→ Finder 判 `ok=false` → 不触发 `post`，**零副作用**）。`set BHF_MOCK_FULL=200` 可放行全量 | `python dev/mock_analyzer.py`（默认 `127.0.0.1:8790`） | 只绑 `127.0.0.1`；不连 B站、不读写真实历史库 |
| `stub_fetcher.py` | **假控制后端**（端口 `8899`）：真实 Analyzer 未开时，验证「实时更新」按钮 → 转发 → 刷新的**控制流闭合**。不真拉数据 | `python dev/stub_fetcher.py` | 只绑 `127.0.0.1:8899` |
| `verify_riskfix.py` | **风险审查修复的自动化验收**（2026-10-01）：第一批 `F-H1` 只绑回环 · `F-H3` 脏 body 返 400 · `F-H2` 坏路径被拒且不落盘 · `N-H1` 失败留痕（`FileNotFoundError` 不记）· `N-L3` 原子写；§8 收尾 `C-H1` body 上限 413 · `C-M1` 纯空白 400 · `C-H2` 同一配置文件的字段不被互相清掉 · `C-M2` span/meta 读失败留痕。**60 项断言** | `python dev/verify_riskfix.py` | 把 `src/` **整目录复制**到临时目录后**原样启动** `python <tmp>/src/server.py`（不 patch、不 mock `main()`）；`ANALYZER_DB` 指向沙箱内不存在的路径 → **不读真实 Analyzer 库**；用 `netstat` 独立核对监听地址；C-H1 用**裸 socket** 手搓「声称 5 MB、不发送 body」的请求（`urllib` 会按 data 长度自动填，捏不出来） |

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

> 阶段 2 要把抓取判据从"读手切模式"换成"读探测 + 策略" —— 那时**先跑纯函数单测**（几秒内知道决策逻辑对不对），再跑集成回归。

`mock_analyzer.py` 单跑时是「手工版」（改配置 → 触发 → 还原，见 `doc/待办-当前阶段.md` §6.2，"想亲眼看一次"时用）；
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
2. **优先"内存覆盖"而不是"改磁盘配置"**。例：`_fetcher_cfg()` 的优先级是 `FETCHER_OVERRIDE`（模块级内存 dict）> 环境变量 > `data/fetcher_config.json` > 默认 → 测试时只改内存即可，**省掉"改配置 → 必须还原"整步**，风险从 🔵 降到 🟢。同理 `server.BACKUP_ROOT`、`server.RUN_DIR`。
3. **必须有反例对照**。只测"该触发时触发了"不够——一个永远返回 `true` 的实现也能通过。要同时测"**不该触发时不触发**"（如 `regression_fallback.py` 的用例 2 / 3）。
4. **副作用函数打桩而不是真跑**。唯一会真写盘的钩子（如 `_after_data_pull` → 真备份 + reload）替换成记录桩，被测的**分支判定逻辑本身保持真实**。
5. **⭐ 假样本测不出"键名漂移"——契约要用真实产出校验**。单测里自己构造的 dict 只能证明"算法对"；若生产代码改了一个字段名（而单测样本没跟着改），单测照样全绿。
   实例（2026-09-26 阶段 1）：`server.probe_connection()` 产出的 finder 段键名是 `state`，而 `store.derive_capabilities()` 读的是 `sessdata` → 独立形态下 `fetch` 恒被判"未知/不可用"；**7 个单测段全绿**，因为样本是我自己写的（两个名字都填了）。是 `--live` 那条"用**真实响应**再喂一次纯函数"的断言把它抓出来的。
   → 规则：**凡是有"两侧约定"的结构（probe ↔ 能力层、请求 ↔ 响应），必须有一条断言拿真实产出（或真实端点的响应）去驱动消费方。**
6. **⭐ 要测"启动期行为"，就把仓库复制一份再跑真进程**。绑定地址、`ensure_rules()`、`_load_source_config()`、`reload_all()` 这些**只在 `main()` 里跑一次**的东西，进程内 patch 测不到，而起真服务又必须动配置/裸奔监听。
   实例（2026-10-01 `verify_riskfix.py`）：把 `src/` **整目录** `copytree` 到临时目录、配一份**空 `data/`** 与 `config.json(web_port=空闲端口)`，再 `subprocess` 跑 `python <tmp>/src/server.py` —— `PROJECT_ROOT` 由 `__file__` 推导，于是**整个实例自然隔离**，一行生产代码都不用改。
   另外把 `ANALYZER_DB` 环境变量也指向沙箱内不存在的路径：**连"配置解析失败回落默认值"这条路也锁在沙箱里**（否则回落会读到真实 Analyzer 库）。
    配合 `netstat -ano` 做**独立于被测代码自称**的核对 —— 这是唯一能证伪"横幅说 127.0.0.1、实际绑 0.0.0.0"的手段。
7. **⭐ 断言里不能藏环境前提**——否则"环境变了"会被读成"代码坏了"。实例（2026-10-01）：`test_capabilities.py --live` 有一条"旧端点仍含 `status`"，而 `status` 是 **Analyzer `/health` 原样透传**的字段 → **Analyzer 一停（8899 不在跑），响应变成优雅降级体，这条就误报失败**；代码一行没坏，排查却要从"是不是我改坏了"绕一圈。
    → 规则：**断言只写"在所有可运行环境下都必须成立"的不变量**；随环境变化的分支要显式分流（如"可达 → 必须含 `status`；不可达 → 必须是降级体且不伪造 `status`"），并在**两种环境下都跑一次**再收工。
    → 推论：凡是"依赖另一个服务在跑"的断言，收尾时**要顺手验一遍对方不在跑的情形**（本次正是零成本验证：Analyzer 本来就没开）。

## 四、本机注意事项

- **HTTP 代理会咬人**：命令行 `curl` 访问 `127.0.0.1` 会走代理并返回 **502**（看着像服务挂了）。→ curl 加 `--noproxy "*"`；Python 脚本在进程内临时 `os.environ.pop` 掉 `http_proxy / https_proxy / all_proxy`（大小写各一份）并设 `no_proxy=127.0.0.1,localhost`，否则 `urllib` 的转发也会被代理吃掉。**网页按钮不受影响。**
- **端口占用**：`8765` = 服务本体 ｜ `8899` = Analyzer ｜ `8790` = `mock_analyzer.py` 默认。
- **`pythonw.exe` 下 `sys.stdout / sys.stderr` 是 `None`**（GUI 子系统无控制台）→ 常驻脚本要兜底，否则静默死掉。

- **文档与文件约定**（改这个仓库的文档时踩过的坑，务必遵守）：
  - **表格单元格内不能有裸 `|`** —— 行内代码的反引号**挡不住** GFM 的列分隔符，整行表格结构会被破坏。→ 用 `·` 或转义 `\|`。
  - **换行必须跟文件一致**：`doc/方案-*.md` / `README.md` / `start.bat` = **CRLF**；`doc/待办-当前阶段.md` / `src/*.py` / `dev/*.py` = **LF**。给 CRLF 文件追加内容**不能直接用行编辑工具写 `\n`**（会混进裸 LF）→ 用脚本按 CRLF 拼接。
  - **探测换行必须用二进制模式读**（`open(p, 'rb')`）：Python **文本模式会把 `\r\n` 静默转成 `\n`**（universal newlines）→ 用文本模式检测换行**永远返回 LF**，再照此写回去，就把整个 CRLF 文件降级成 LF 了（2026-09-26 实际踩到，两份方案文档被转成 LF 后需手工恢复）。
  - **别拿 `git show` 判断工作区换行**：本仓库 `core.autocrlf=true`，仓库内存储恒为 LF，checkout 到工作区才变 CRLF → 判断格式一律**以工作区文件为准**。
  - **`.bat` 一律纯 ASCII + CRLF，且不要写 `chcp 65001`** —— 批处理中途切代码页是 cmd 的已知 bug，会吃掉后续行的首字符；中文 Windows 双击运行的 bat 若有中文，用 **GBK** 编码。
  - **大段中文别用 Bash heredoc 写 Python**（极易触发 `SyntaxError: Perhaps you forgot a comma`）→ 先用 Write 落成临时 `.md`，再用短脚本拼接。

## 五、跑法速查

```cmd
cd /d D:\Programs\Share\BilibiliHistoryFinder

python dev\test_capabilities.py           rem 阶段1 纯函数单测（零 IO，秒级）
python dev\test_capabilities.py --live    rem 追加 /api/capabilities 端到端冒烟
python dev\regression_restart.py          rem ⑥ 重启链路（42/42/42 + 旧设计 0/0/0 对照）
python dev\regression_fallback.py         rem ② 自动回退（3/3 + 零污染核对）
python dev\verify_riskfix.py              rem 风险审查修复验收（60 项，真进程 + 隔离副本）

rem 想亲眼看回退过程时（手工版，记得还原 base）：
python dev\mock_analyzer.py
curl --noproxy "*" -X POST -H "Content-Type: application/json" -d "{\"base\":\"http://127.0.0.1:8790\"}" http://127.0.0.1:8765/api/fetcher-config
curl --noproxy "*" http://127.0.0.1:8765/api/fetcher-trigger
curl --noproxy "*" -X POST -H "Content-Type: application/json" -d "{\"base\":\"http://localhost:8899\"}" http://127.0.0.1:8765/api/fetcher-config
```
