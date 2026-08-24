# BilibiliHistoryFinder — B站历史查看器（原型版）

> **状态：原型阶段（Prototype）**。本仓库为「续看 / 历史筛选分类」规则的**原型实现**：用纯 Python 标准库把 B站观看历史抓到本地、长期留存，并用一个类 B站网页端的界面浏览、搜索、筛选，找出「还没看完」的视频。
> 后续正式版路线见 `doc/正式版方案.md`；本 README 只描述**当前原型**的使用与结构。

---

## 1. 它能做什么

- **本地留存**：历史记录存进本地 SQLite，突破 B站官方「近 ~1000 条 / 3 个月滚动覆盖」的限制，从你开始用它的那天起一条不漏。
- **类网页端浏览**：封面 + 标题 + UP主 + 相对时间 + 进度条，可点击跳转 B站观看。
- **「需要观看」视图**：自动筛出"还没看完"（`progress < 95%` 或未完成）且未被规则跳过的视频。
- **续看规则引擎（auto_skip）**：按进度 / 时长 / 类型 / UP主等条件**批量标记"不需要观看"**，规则存于 `data/rules.json`，在 ⚙ 右侧抽屉中编辑。
- **三态跳过**：空 / 手动跳过 / 自动跳过，互斥且带角标；自动覆盖手动（最后操作优先）。
- **批量操作**：批量跳过 / 恢复 / 隐藏。
- **增量同步**：首次全量建基线，之后只拉新记录，快且不踩限流。

---

## 2. 快速开始（Windows）

### 2.1 准备登录态（SESSDATA）
1. 用浏览器登录 B站（bilibili.com）。
2. 按 `F12` → **Application** → 左侧 **Cookies** → 选 `https://.bilibili.com` 域名。
3. 找到名为 **`SESSDATA`** 的条目，复制它的 **Value**（一长串字符）。
4. 打开仓库根 `config.json`，把值填进 `SESSDATA`：
   ```json
   {
     "SESSDATA": "这里粘贴你的SESSDATA值",
     "page_size": 30,
     "request_interval": 0.3,
     "db_path": "data/bilibili_history.db"
   }
   ```
   > ⚠️ SESSDATA 是登录凭证，泄露等于别人能登录你的账号，**请勿分享、勿提交到公开仓库**。它会过期（几天到几十天不等），过期后同步失败需重新取值。

### 2.2 启动（推荐用 bat）
仓库根目录已提供 `start.bat` / `stop.bat`（前端改动读盘即生效，无需重启即可刷新看效果）：
- 双击 **`start.bat`**：自动探测本机 Python（兼容无系统级 Python 的情况）、前台启动服务并持续打印状态（含续看规则「待应用 / 脏计数」）。
- 双击 **`stop.bat`**：关闭当前 8765 端口服务。
- 浏览器打开 **http://127.0.0.1:8765** 即可使用。

### 2.3 手动启动（等价命令）
```bash
# 1) 拉取历史（首次全量建基线；之后可只跑 Web 服务，点界面"同步数据"增量拉取）
python src/collector.py --config config.json

# 2) 启动 Web 服务（默认端口 8765）
python src/server.py
```

---

## 3. 界面与核心操作

顶部工具栏分四组（类型 / 视图 / 浏览筛选 / 搜索 + 规则/批量/同步），右侧 ⚙ 抽屉默认隐藏：

- **类型 tab**（综合 / 视频 / 直播 / 专栏）：按内容类型筛选。
- **视图 tab**（全部 / 需要观看 / 已跳过 / 已搁置）：按"续看生命周期"切换。
- **筛选 ▾**：时长 / 时间 / 设备 / 含存档的**临时浏览条件**（不落库，刷新即还原）。
- **搜索**：按标题或 UP主名实时搜索。
- **⚙ 续看规则**：右侧抽屉编辑规则；橙色圆点＝待应用，数字＝脏计数（自上次应用以来未标记的新记录）。
- **同步数据 / 全量重建**：增量同步 / 强制全量校准"仅本地存档"标记。

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
├── collector.py          # 采集器：SESSDATA + history/cursor 分页，按 kid 去重落 SQLite
├── server.py             # 本地 Web 服务（标准库 http.server）：/api/* 提供历史/规则/应用/跳过/同步
├── src/web/              # 前端单页：index.html / app.js / style.css（零框架、零 npm）
├── data/
│   ├── bilibili_history.db   # 主数据库（全部历史 + 跳过标记）
│   ├── rules.json            # 续看规则
│   ├── config.json           # 配置（含 SESSDATA）
│   └── covers/               # 封面图片缓存
├── start.bat / stop.bat  # 启动 / 关闭（自动探测 Python）
└── doc/                  # 文档（归档评估 + 正式版方案）
```

- **采集**：`collector.py` 仅用 Python 标准库（`urllib.request` / `sqlite3` / `json` / `datetime` / `argparse`），**无第三方依赖**。
- **服务**：`server.py` 用标准库 `http.server` 起本地静态服务，**零依赖、零构建**。
- **前端**：原生 HTML/CSS/JS 单页，可完全离线运行。
- **数据通路**：`SESSDATA + cursor 分页接口 + 本地 SQLite`；`progress` 覆盖更新（同一 kid 二次观看进度变大时覆盖，而非简单跳过）。

---

## 5. 数据文件

| 文件 | 作用 |
|---|---|
| `data/bilibili_history.db` | 主数据库（全部历史记录 + 跳过标记） |
| `data/config.json` | 配置，含 `SESSDATA` 登录态 |
| `data/rules.json` | 续看规则（由 ⚙ 抽屉编辑，不直接手改） |
| `data/sync_progress.json` / `sync_result.json` | 同步进度与完成判定 |
| `data/auto_skip_progress.json` | 规则应用进度（运行时产生） |
| `data/covers/` | 封面图片缓存 |

> 💡 **备份建议**：`bilibili_history.db` 是全部留存数据的唯一载体，重要前请复制（如 `bilibili_history.db.2026-08-24.bak`）。`config.json` 含凭证，备份时注意保密。

---

## 6. 已知限制（原型）

- **只能"从现在起"留存**：B站端已被上限顶掉的过去历史无法恢复（服务端限制，任何方案都救不回）；原型价值是"以后一条不漏"。
- 单账号；多账号为后续阶段。
- SESSDATA 过期需手动更新，暂无前端提示/重填 UI。
- 无一键备份/导出（计划后续实现）。
- 设备类型（`dt`）仅为占位标签（B站未公开真实设备映射）。
- 同步内自动套用规则暂缓，当前以"应用规则"为统一重校准入口。

---

## 7. 文档索引

| 文档 | 内容 |
|---|---|
| `doc/archive/哔哩哔哩历史查看器-使用说明.md` | 面向使用者的完整操作手册 |
| `doc/archive/哔哩哔哩历史查看器-原型方案.md` | 技术路线、可行性、分阶段目标 |
| `doc/archive/哔哩哔哩历史查看器-需求评估.md` | 原始需求与评估 |
| `doc/archive/测试版评估文档.md` | 实现路线评估（测试版，已归档） |
| `doc/正式版方案.md` | 正式版开发方案（含统一数据契约、路线选型） |

---

*原型版 README · 适用版本：本地 Web 版（零第三方依赖，纯 Python 标准库）。*
