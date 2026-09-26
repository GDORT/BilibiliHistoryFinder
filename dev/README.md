# `dev/` —— 测试资产目录

> 位置：`BilibiliHistoryFinder/dev/` ｜ 整理：2026-09-26
> 定位：**开发期测试资产**。`src/` 下的运行时代码**不 import 这里**，整目录删掉也不影响服务运行。

## 一、资产清单

| 文件 | 干什么 | 跑法 | 边界 |
| --- | --- | --- | --- |
| `regression_restart.py` | **⑥ 重启链路的常驻回归**。真起 `ThreadingHTTPServer` + 主线程 `serve_forever` + 工作线程发起重启（**生产拓扑**），连跑 3 次断言退出码均为哨兵 `42`；另含看门狗超时（`shutdown` 卡死 30s → 3s 顶出）、`_safe_print` 冻结对照、**旧设计对照**（`0/0/0`，线上 bug 的复现） | `python dev/regression_restart.py` | 只绑 `127.0.0.1:0`；写盘全部重定向临时目录 |
| `regression_fallback.py` | **② 自动回退（增量 → 全量）的端到端回归**。真起 `server.Handler` 发 HTTP GET；三条用例互相对照（缺基线**必须**回退 / 基线正常**不得**回退 / 其它错误**不得**回退） | `python dev/regression_fallback.py` | 同上；**base 走内存覆盖，`data/fetcher_config.json` 一字节不动** |
| `mock_analyzer.py` | **假 Analyzer**：增量接口固定回「未找到本地历史记录」以逼出回退分支；全量默认 `503`（→ Finder 判 `ok=false` → 不触发 `post`，**零副作用**）。`set BHF_MOCK_FULL=200` 可放行全量 | `python dev/mock_analyzer.py`（默认 `127.0.0.1:8790`） | 只绑 `127.0.0.1`；不连 B站、不读写真实历史库 |
| `stub_fetcher.py` | **假控制后端**（端口 `8899`）：真实 Analyzer 未开时，验证「实时更新」按钮 → 转发 → 刷新的**控制流闭合**。不真拉数据 | `python dev/stub_fetcher.py` | 只绑 `127.0.0.1:8899` |

### 关系图

```
regression_fallback.py ──复用 MockHandler──▶ mock_analyzer.py
        │                                          (独立假后端)
        │ 起 server.Handler（生产代码，非副本）
        ▼
  GET /api/fetcher-trigger ──转发──▶ http://127.0.0.1:<随机端口>
```

`mock_analyzer.py` 单跑时是「手工版」（改配置 → 触发 → 还原，见 `doc/待办-当前阶段.md` §6.2，"想亲眼看一次"时用）；
被 `regression_fallback.py` 复用时是「自动版」（内存覆盖，不改磁盘）。

## 二、四条共同安全约定

1. **绝不绑对外地址**：一律 `127.0.0.1` + 随机端口（`0`）。用完即 `shutdown()` + `server_close()`，不留后台进程。
2. **不碰历史数据**：不读写 `data/*.db` 与 Analyzer 主库；假后端不连 B站。
3. **写盘全部重定向临时目录**，且要**逐个覆盖**：
   `RUN_DIR` / `RESTARTS_FILE` / `RESTART_LOG` / `RELAUNCH_FLAG` 都是 `server.py` **模块顶层**用 `RUN_DIR` 拼死的常量——**改 `RUN_DIR` 不会连带改它们**。
4. **跑完核对零污染**：比对文件 md5 / `data/run/` 与 `data/backup/` 目录列表。

## 三、写新回归脚本的四条经验（都是踩出来的）

1. **拓扑必须与生产同构**。要测一段写在 HTTP handler 里的逻辑（如 `/api/fetcher-trigger` 的回退分支），就**真起 handler 发真请求**，别把那段 `if` 抄进脚本——抄出来的是副本，线上照错。
   上任教训：`_verify_restart.py` 最初直接在主线程调 `_restart_after_response()`，天然拿到 42，**放过了真 bug**（生产里它在守护线程，退出码被吞成 0）。
2. **优先"内存覆盖"而不是"改磁盘配置"**。例：`_fetcher_cfg()` 的优先级是 `FETCHER_OVERRIDE`（模块级内存 dict）> 环境变量 > `data/fetcher_config.json` > 默认 → 测试时只改内存即可，**省掉"改配置 → 必须还原"整步**，风险从 🔵 降到 🟢。同理 `server.BACKUP_ROOT`、`server.RUN_DIR`。
3. **必须有反例对照**。只测"该触发时触发了"不够——一个永远返回 `true` 的实现也能通过。要同时测"**不该触发时不触发**"（如 `regression_fallback.py` 的用例 2 / 3）。
4. **副作用函数打桩而不是真跑**。唯一会真写盘的钩子（如 `_after_data_pull` → 真备份 + reload）替换成记录桩，被测的**分支判定逻辑本身保持真实**。

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

python dev\regression_restart.py     rem ⑥ 重启链路（42/42/42 + 旧设计 0/0/0 对照）
python dev\regression_fallback.py    rem ② 自动回退（3/3 + 零污染核对）

rem 想亲眼看回退过程时（手工版，记得还原 base）：
python dev\mock_analyzer.py
curl --noproxy "*" -X POST -H "Content-Type: application/json" -d "{\"base\":\"http://127.0.0.1:8790\"}" http://127.0.0.1:8765/api/fetcher-config
curl --noproxy "*" http://127.0.0.1:8765/api/fetcher-trigger
curl --noproxy "*" -X POST -H "Content-Type: application/json" -d "{\"base\":\"http://localhost:8899\"}" http://127.0.0.1:8765/api/fetcher-config
```
