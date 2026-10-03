"use strict";

const PAGE = 60;
let offset = 0;
let total = 0;
let loading = false;

/* ======================================================================
 * 阶段 4 · 能力渲染（`方案.md` D2 / D7）
 * ====================================================================== */

/** 「置灰 → 隐藏」的单点常量（`方案.md` D7）：`T2-A` 已定为**只置灰、不隐藏**
 *  （保留全部入口的可见性，让用户随时看得到「这个功能现在用不了、为什么」）。
 *  阶段 6 若要改为隐藏，**只需改这一行**。 */
const UNAVAILABLE_MODE = "gray";

/** 能力 → DOM 清单（**唯一映射表**）。定稿于 2026-10-03（`T14`）——
 *  逐个选择器对着 `index.html` 实际 96 个 `id` 校验过存在性。
 *  ⚠️ **任何改动都必须重跑 `dev/test_capabilities.py` 的 `T2.8` 选择器校验** ——
 *  历史教训：老版示例的 `#selfCheckBtn` 与 `.tab-analysis` 都指向不存在的 DOM，
 *  会**静默失效**（`querySelectorAll` 返回空集、不报错）。 */
const CAP_DOM = {
  // ⚠️ **`fetch` 故意是空集**（2026-10-03 用户裁定「Finder 自己能抓」）。
  //   独立形态下 `derive_capabilities()` 判 `fetch.available=true, owner="finder"`
  //   （理由：Finder 自己能抓）—— 这是**对的**，但它**不意味着 Analyzer 的中继按钮该亮着**。
  //   「抓取」在独立形态下由 `POST /api/sync` 承担（走 collector，见 `方案.md` D3），
  //   那个入口是 `#syncBtn`（归 `sync`，恒可用）→ 故 `fetch` 无需挂任何 DOM。
  //   曾经把 `#anRealtimeBtn`/`#anFullBtn` 挂在这里，导致：按钮亮着、点了必 409
  //   （它们打的是 Analyzer-only 的 `/api/fetcher-trigger`）—— 见 `ANALYZER_ONLY_DOM`。
  fetch:     [],
  sync:      [],                  // 恒可用（纯本地读源库 + 独立形态下的 collector 抓取）→ 恒不置灰
  remark:    [],                  // ⚠️ 动态渲染，见 `updateRemarkAvailability()`
  // ⚠️ **不含 `#anLocalExportBtn`** —— 它是「独立形态下唯一还能用的导出出口」，
  //    随 `export` 一起置灰等于把刚加的逃生通道堵死（D7 的「先灰」在这里必须破例）。
  //    故它**不进能力表**、**不参与门控**，恒可用。
  export:    ["#anExportBtn", "#anDbBtn"],
  images:    ["#anImgBtn", "#anImgStartBtn", "#anImgStopBtn"],
  integrity: ["#anDataBtn"],
  backup:    [],                  // 恒可用（Finder 自有快照）
};

/** **端点归属**维度：物理上依赖 Analyzer 服务在线的 DOM（与「能力」是两回事）。
 *
 * 为什么要单独一维（2026-10-03 审查 §七.1 的教训）：
 *   能力表回答「**这件事**能不能做」，而这一维回答「**这个按钮打的端点**在不在 Analyzer 上」。
 *   二者会交叉出「能力说可用、端点却 409」的矛盾 —— 独立形态 + Finder 凭证有效时
 *   `fetch.available=true`（Finder 自己能抓）但 `#anRealtimeBtn` 必 409。
 *   把它们混在一张表里就必然出错，故**拆开**：`CAP_DOM` 管能力，本表管端点归属。
 *
 * 判据用 `connection.analyzer.ok`（**业务级** ＝ `/health` HTTP 成功且可达），
 * 与后端 409 门控**同一判据** → 不会再出现「按钮亮着、点了必失败」。
 */
const ANALYZER_ONLY_DOM = ["#anRealtimeBtn", "#anFullBtn"];
const ANALYZER_ONLY_HINT = "该入口转发给 Analyzer（/api/fetcher-trigger）—— Analyzer 不可用时无法使用。"
  + "独立形态下请改用顶部「同步数据」（走 Finder 自己的采集）";

/** 置灰时的 `title` 提示：让用户知道「为什么灰」＋「怎么才能用上」。
 *  ⚠️ **无 `fetch` 条目** —— `CAP_DOM.fetch` 是空集，抓取入口的提示走 `ANALYZER_ONLY_HINT`。 */
const CAP_HINT = {
  remark:    "备注写回 Analyzer 主库 —— Analyzer 不可用时无法保存",
  export:    "导出 Excel / 整库走 Analyzer；独立形态下请改用「本地库」按钮",
  images:    "图片批量下载由 Analyzer 执行 —— Analyzer 不可用时无法使用",
  integrity: "数据自检由 Analyzer 执行 —— Analyzer 不可用时无法使用",
};

/** 最近一次拿到的能力表（供卡片渲染层判断 `remark`）。 */
let CAPS = {};

/** 把「不可用」按 `UNAVAILABLE_MODE` 施加到一组元素。 */
function _applyTo(selectors, available, hint) {
  (selectors || []).forEach((sel) => {
    document.querySelectorAll(sel).forEach((el) => {
      if (available) {
        el.classList.remove("cap-disabled");
        el.removeAttribute("aria-disabled");
        el.title = el.dataset.capOriginTitle || "";
        if (UNAVAILABLE_MODE === "hide") el.classList.remove("hidden");
      } else {
        // 首次置灰时把原 title 存下来，恢复时才能还原（否则提示会被覆盖丢失）
        if (!el.dataset.capOriginTitle) el.dataset.capOriginTitle = el.title || "";
        el.classList.add("cap-disabled");
        el.setAttribute("aria-disabled", "true");
        el.title = hint || "";
        if (UNAVAILABLE_MODE === "hide") el.classList.add("hidden");
      }
    });
  });
}

/** 表驱动应用能力表（`方案.md` D7）。`caps` 取 `GET /api/capabilities` 的 `capabilities`。 */
function applyCapabilities(caps, analyzerOk) {
  CAPS = caps || {};
  Object.keys(CAP_DOM).forEach((cap) => {
    const info = CAPS[cap] || { available: false, reason: "能力未知（尚未探测）" };
    _applyTo(CAP_DOM[cap], !!info.available, CAP_HINT[cap] || info.reason || "");
  });
  // 端点归属维度：**业务级**判据（`analyzer.ok`），与后端 409 门控同源。
  // ⚠️ 不能用 `caps.fetch.available` —— 独立形态下它为 true（Finder 自己能抓），
  //    但这些按钮打的是 Analyzer 中继 → 会「亮着但必 409」。这正是审查 §七.1 指出的缺陷。
  if (analyzerOk !== undefined) {
    _applyTo(ANALYZER_ONLY_DOM, !!analyzerOk, ANALYZER_ONLY_HINT);
  }
  updateRemarkAvailability();   // `remark` 动态渲染，单独处理
}

/** `remark` 单独处理：每次列表渲染时新生成，不在 `CAP_DOM` 的固定选择器里。 */
function updateRemarkAvailability() {
  const info = CAPS["remark"] || { available: false, reason: "" };
  document.querySelectorAll(".remark-text").forEach((el) => {
    if (info.available) {
      el.classList.remove("cap-disabled");
      el.removeAttribute("aria-disabled");
      el.title = el.dataset.capOriginTitle || "";
    } else {
      if (!el.dataset.capOriginTitle) el.dataset.capOriginTitle = el.title || "";
      el.classList.add("cap-disabled");
      el.setAttribute("aria-disabled", "true");
      el.title = CAP_HINT["remark"] || info.reason || "";
    }
  });
}

// 当前筛选状态
const state = {
  biz: "",       // tab: 综合/视频/直播/专栏
  q: "",         // 搜索关键词
  dur: "",        // 时长区间 "min-max"
  timeRange: "",  // 时间快捷预设 today/yesterday/week/""
  dt: "",         // 设备 dt 值
  view: "all",    // 视图：all=全部 / needs=需要观看 / skipped=已跳过 / stale=已搁置
  archived: false,
  dateFrom: "",
  dateTo: "",
};

const $ = (id) => document.getElementById(id);
const enc = (s) => encodeURIComponent(s);
const listEl = $("list");
const emptyEl = $("empty");
const loadMoreEl = $("loadMore");
const statsEl = $("stats");

// 批量勾选模式状态
let batchMode = false;        // 是否进入批量模式
let batchAction = "skip";     // "skip"=批量跳过 / "restore"=批量恢复 / "delete"=批量删除(隐藏)

// 续看规则引擎（data/rules.json）
let rulesData = null;         // { rules: [...] }
let editingIdx = -1;          // 抽屉中当前展开编辑的规则下标
let rulesDirty = false;       // 是否有未保存/未应用编辑

// 规则抽屉开关状态
let drawerOpen = false;

/* ====== 工具函数 ====== */

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

/* #36 系统备注识别 —— Analyzer 会把「收藏 / 点赞 / 投币」补进历史库（因 B站观看历史只保留近三个月），
   并写一条形如「互动补充：收藏」的备注标明来源（analyzer 侧 interaction_records.py 的
   HISTORY_IMPORT_REMARK_PREFIX）。Finder 不写这个字段，只在展示上把它和用户自写备注区分开，
   避免用户误在溯源标记上写字把它覆盖掉。 */
const SYS_REMARK_PREFIX = "互动补充";

function isSystemRemark(s) {
  return typeof s === "string" && s.indexOf(SYS_REMARK_PREFIX) === 0;
}

function remarkClass(remark) {
  if (!remark) return "remark-text empty";
  return "remark-text" + (isSystemRemark(remark) ? " sys" : "");
}

function remarkTitle(remark) {
  return isSystemRemark(remark)
    ? "Analyzer 自动写入的溯源标记（非你手写）；点击编辑会覆盖它"
    : "点击编辑备注（写回 Analyzer 主库，与 Frontend/官网互通）";
}

// 时长（秒）→ MM:SS 或 H:MM:SS
function fmtDur(s) {
  if (s == null || s <= 0) return "";
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${p(m)}:${p(sec)}` : `${m}:${p(sec)}`;
}

// 观看时间 → 相对格式：今天 11:20 / 昨天 11:20 / MM-DD HH:MM
function relTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const today = startOfDay(new Date());
  const yest = startOfDay(new Date()); yest.setDate(yest.getDate() - 1);
  const dd = startOfDay(d);
  if (dd.getTime() === today.getTime()) return `今天 ${hm}`;
  if (dd.getTime() === yest.getTime()) return `昨天 ${hm}`;
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}

// 进度百分比
function pctOf(it) {
  if (it.progress == null || it.duration == null || it.duration <= 0) return null;
  if (it.progress === -1) return 100;
  return Math.round((it.progress / it.duration) * 100);
}

// 时间范围预设 → 返回 { from_ts, to_ts } 或 null
function timeRangeToTs(range) {
  const now = new Date();
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const startOfDay = (d) => Math.floor(day(d).getTime() / 1000);
  const endOfDay = (d) => startOfDay(d) + 86400 - 1;

  switch (range) {
    case "today":
      return { from: startOfDay(now), to: endOfDay(now) };
    case "yesterday": {
      const y = new Date(now); y.setDate(y.getDate() - 1);
      return { from: startOfDay(y), to: endOfDay(y) };
    }
    case "week": {
      const w = new Date(now); w.setDate(w.getDate() - 6);
      return { from: startOfDay(w), to: endOfDay(now) };
    }
    default:
      return null;
  }
}

// 时长预设 → 返回 { min_sec, max_sec } 或 null
function durToSec(range) {
  if (!range) return null;
  const [min, max] = range.split("-").map(Number);
  return { min, max };
}

/* ====== 构建 API 查询体（统一走 POST /api/query） ====== */

// 高级筛选器状态（engine.evaluate_filter 所用 spec）
const advFilter = {
  enabled: false,   // 是否启用高级筛选（false 时仅用快捷视图/搜索）
  logic: "AND",     // 顶层分组间布尔：AND / OR / NOR
  negate: false,    // 整体取反
  groups: [],        // [{ logic: "AND"|"OR", conditions: [{field, op, value}] }]
  having: null,      // { groupBy, op, value }
};

// 排序状态
let sortFields = [];   // [{ field, dir: "asc"|"desc" }]

// 选中的保存视图
let activeViewId = null;

function buildQueryBody(reset) {
  if (reset) offset = 0;
  const body = {
    view: state.view || "all",
    q: state.q || "",
    limit: PAGE,
    offset: offset,
    filter: advFilter.enabled ? advFilter : null,
    sort: sortFields,
    lists: {},
  };
  // 黑白名单引用（在筛选条件里以 in_list/not_in_list 引用，这里塞入值集）
  if (advFilter.enabled) {
    body.lists = loadListValuesForFilter();
  }
  // 时间范围筛选：把 state 里的时间条件塞进请求体（修复此前死代码——设置了 state 却从不发出）
  let tf = null, tt = null;
  if (state.timeRange) {
    const r = timeRangeToTs(state.timeRange);
    if (r) { tf = r.from; tt = r.to; }
  }
  if (state.dateFrom) tf = Number(state.dateFrom);
  if (state.dateTo)   tt = Number(state.dateTo);
  if (tf != null || tt != null) {
    body.time_from = tf;
    body.time_to = tt;
  }
  return body;
}

function loadListValuesForFilter() {
  // 从已加载名单构建 {列表id: [values]}，供 in_list/not_in_list 引用
  const out = {};
  (window.__lists || []).forEach((l) => { out[l.id] = l.values || []; });
  return out;
}

/* ====== 卡片渲染 ====== */

function skipBadgeHtml(it) {
  const skipState = it.skip_state || "";
  const reason = it.auto_skip_reason || "";
  if (skipState === "manual") {
    return `<span class="badge-skip manual cancelable" data-kid="${escapeHtml(it.kid)}" title="手动跳过：点击取消（同步不会回退其进度）">已跳过·手动</span>`;
  }
  if (skipState === "auto") {
    const label = reason.includes("::") ? reason.split("::")[1] : "";
    if (reason.startsWith("stale::")) {
      return `<span class="badge-skip stale cancelable" data-kid="${escapeHtml(it.kid)}" title="按规则自动搁置：点击取消">已搁置·${escapeHtml(label)}</span>`;
    }
    return `<span class="badge-skip auto cancelable" data-kid="${escapeHtml(it.kid)}" title="按规则自动跳过：点击取消">已跳过·${escapeHtml(label)}</span>`;
  }
  return "";
}

function cardHtml(it) {
  const pct = pctOf(it);
  const finished = pct != null && pct >= 95;
  const coverSrc = "/cover/" + encodeURIComponent(it.kid);
  const webUrl = it.bvid ? `https://www.bilibili.com/video/${it.bvid}` : (it.uri || "#");

  // 左上角徽标：直播中 / 番剧 / 专栏
  let tlBadge = "";
  if (it.business === "live" && it.live_status == 1) tlBadge = '<span class="badge-tl live">直播中</span>';
  else if (it.business === "pgc") tlBadge = '<span class="badge-tl pgc">番剧</span>';
  else if (it.business === "article") tlBadge = '<span class="badge-tl article">专栏</span>';

  // 右下角：已看完 或 观看时长/视频时长
  let durOverlay = "";
  if (pct != null) {
    if (finished) {
      durOverlay = '<span class="badge-done">已看完</span>';
    } else {
      const w = fmtDur(it.progress), d = fmtDur(it.duration);
      durOverlay = (w && d)
        ? `<span class="dur">${w} / ${d}</span>`
        : (d ? `<span class="dur">${d}</span>` : "");
    }
  }
  // 底部进度条（观看进度百分比）
  const pbar = (pct != null) ? `<div class="pbar"><i style="width:${pct}%"></i></div>` : "";

  // 跳过状态徽标（含 manual/auto + reason 原因）
  const skipState = it.skip_state || "";
  const skipBadge = skipBadgeHtml(it);
  // 右上角（未开播等）：有跳过徽标时让位
  let trBadge = "";
  if (!skipState && it.business === "live" && it.live_status != 1) trBadge = '<span class="badge-tr">未开播</span>';

  // 左下角：仅本地存档徽标
  const archBadge = (it.archived_only == 1)
    ? '<span class="badge-arch" title="B站端已无此记录，仅本地留存；存档状态仅初始全量校准">仅本地存档</span>'
    : "";

  // 操作按钮
  let actionBtn;
  if (state.view === "needs") {
    if (skipState) {
      actionBtn = `<button class="skip-toggle on" data-kid="${escapeHtml(it.kid)}" data-act="cancel" title="已标记为不需要观看，点击恢复">取消跳过</button>`;
    } else {
      actionBtn = `<button class="skip-toggle" data-kid="${escapeHtml(it.kid)}" data-act="skip" title="标记为不需要观看（同步不会回退其进度）">不需要看?</button>`;
    }
  } else if (state.view === "skipped" || state.view === "stale") {
    actionBtn = `<button class="skip-toggle on restore-auto" data-kid="${escapeHtml(it.kid)}" data-act="restore-auto" title="按规则自动跳过/搁置，点击恢复（取消自动标记）">恢复</button>`;
  } else {
    actionBtn = `<button class="del-btn" title="隐藏此条" data-kid="${escapeHtml(it.kid)}">🗑</button>`;
  }

  // 批量勾选框（仅批量模式显示；按模式限定可勾选集合）
  let cb = "";
  if (batchMode) {
    const eligible = batchEligible(skipState);
    cb = `<label class="batch-cb-wrap"><input type="checkbox" class="batch-cb" data-kid="${escapeHtml(it.kid)}" ${eligible ? "" : "disabled"} /></label>`;
  }

  return `
  <li class="item" data-skip="${skipState}">
    ${cb}
    <a class="thumb" href="${escapeHtml(webUrl)}" target="_blank" rel="noopener">
      <img class="cover" src="${coverSrc}" alt="" loading="lazy" onerror="this.style.visibility='hidden'" />
      ${tlBadge}
      ${trBadge}
      ${skipBadge}
      ${durOverlay}
      ${archBadge}
      ${pbar}
    </a>
    <div class="meta">
      <div class="title-row">
        <a class="title" href="${escapeHtml(webUrl)}" target="_blank" rel="noopener">${escapeHtml(it.title)}</a>
        ${actionBtn}
      </div>
      <div class="remark-row" data-kid="${escapeHtml(it.kid)}" data-bvid="${escapeHtml(it.bvid || "")}" data-view-at="${it.view_at || 0}">
        <span class="${remarkClass(it.remark)}" title="${remarkTitle(it.remark)}">${it.remark ? escapeHtml(it.remark) : "＋ 添加备注"}</span>
      </div>
    </div>
    <div class="card-bottom">
      <span class="up">${escapeHtml(it.author_name || "未知UP")}</span>
      <span class="time">${relTime(it.view_at)}</span>
    </div>
  </li>`;
}

/* ====== 加载与渲染 ====== */

function render(data) {
  total = data.total || 0;
  if (offset === 0) listEl.innerHTML = "";
  if (!data.items || data.items.length === 0) {
    if (offset === 0) emptyEl.classList.remove("hidden");
    loadMoreEl.classList.add("hidden");
    statsEl.textContent = `共 ${total} 条`;
    return;
  }
  emptyEl.classList.add("hidden");
  listEl.insertAdjacentHTML("beforeend", data.items.map(cardHtml).join(""));
  offset += data.items.length;
  statsEl.textContent = `共 ${total} 条，已显示 ${Math.min(offset, total)}`;
  loadMoreEl.classList.toggle("hidden", offset >= total);
  if (batchMode) updateBatchEligibility();
  updateRemarkAvailability();   // 阶段 4：新插入的卡片也要按 `remark` 能力置灰
}

async function load(reset) {
  if (loading) return;
  if (reset) offset = 0;
  loading = true;
  try {
    const res = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildQueryBody(!!reset)),
    });
    const data = await res.json();
    render(data);
  } catch (e) {
    toast("加载失败：" + e.message);
  } finally {
    loading = false;
  }
}

/* ====== Toast ====== */
let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2500);
}

/* ====== 409 统一消费（阶段 4 · `T10`） ======
 * 阶段 3 起，Analyzer-only 端点在「确定不可用」时返 **409**（而非转发失败 502），
 * 响应体形如 `{ok:false, error, reason, capability, available:false, owner:null}`。
 * 此前各调用点各自 `toast` → 同一句「需要 Analyzer」会在十几处各写一遍。
 * 故统一到这一个函数：**按 `capability` 给出对应提示，并顺手把该能力的入口置灰**
 * （点了必失败的按钮当场变灰，比只弹一句提示更符合「确定」的直觉）。 */
function handleApiError(status, payload) {
  const p = payload || {};
  if (status === 409) {
    const cap = p.capability || "";
    const msg = p.error || p.reason || "当前能力不可用（Analyzer 未通过探测）";
    if (cap) {
      // 立刻把这一项标成不可用（本地乐观更新，不必等下一轮轮询）
      CAPS[cap] = { available: false, owner: null, reason: p.reason || msg };
      _applyTo(CAP_DOM[cap], false, CAP_HINT[cap] || msg);
      if (cap === "remark") updateRemarkAvailability();
    }
    toast(msg);
    return msg;
  }
  if (status === 502) return "Analyzer 转发失败（" + (p.error || "无详情") + "）";
  return p.error || ("HTTP " + status);
}

/* ====== Tab 切换（综合/视频/直播/专栏）===== */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.biz = btn.dataset.biz || "";
    load(true);
  });
});

/* ====== 视图切换（全部 / 需要观看 / 已跳过 / 已搁置）===== */
function setView(v) {
  state.view = v;
  document.querySelectorAll(".vtab").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === v));
  syncBatchKindToView();
  load(true);
}
document.querySelectorAll(".vtab").forEach((btn) => {
  btn.addEventListener("click", () => setView(btn.dataset.view));
});

/* ====== 浏览筛选 popover（时长/时间/设备/含存档，不落库）===== */
$("browseToggle").addEventListener("click", (e) => {
  e.stopPropagation();
  const bp = $("browsePanel");
  const open = bp.classList.toggle("hidden");
  // toggle 返回 false 表示已显示（open=true）
  $("browseToggle").classList.toggle("open", !open);
  $("archChk").checked = state.archived;
});
document.addEventListener("click", (e) => {
  const bp = $("browsePanel");
  if (bp.classList.contains("hidden")) return;
  const t = e.target;
  if (t === $("browseToggle") || $("browseToggle").contains(t) || bp.contains(t)) return;
  bp.classList.add("hidden");
  $("browseToggle").classList.remove("open");
});
$("archChk").addEventListener("change", () => {
  state.archived = $("archChk").checked;
  load(true);
});

/* ====== 续看规则引擎：字段词表与控件生成（data/rules.json） ====== */
const FIELD_LABELS = {
  business: "类型", duration: "时长(秒)", author_name: "UP主名",
  author_mid: "UP主ID", progress_pct: "进度比例(0-1)", progress_sec: "进度(秒)",
  view_at_age_days: "距今(天)", title: "标题",
};
/* ====== 高级筛选器：扩展字段词表（覆盖更多维度） ====== */
const ADV_FIELD_LABELS = {
  business: "类型", duration: "时长(秒)", author_name: "UP主名",
  author_mid: "UP主ID", title: "标题", progress_pct: "进度比例(0-1)",
  progress_sec: "进度(秒)", view_at_age_days: "距今(天)",
  tag_name: "标签", remark: "备注", main_category: "分区",
  is_fav: "已收藏", live_status: "直播状态", videos: "分P数",
  progress: "进度(秒绝值)", view_at: "观看时间", dt: "设备",
};
const ADV_FIELD_TYPE = {
  business: "types", duration: "num", author_name: "text", author_mid: "text",
  title: "text", progress_pct: "num", progress_sec: "num", view_at_age_days: "num",
  tag_name: "text", remark: "text", main_category: "text",
  is_fav: "num", live_status: "num", videos: "num",
  progress: "num", view_at: "num", dt: "num",
};
const ADV_OP_BY_FIELD = {
  business: [["in", "属于"], ["not_in", "不属于"]],
  duration: [">=", "≥", "<=", "≤", ">", ">", "<", "<", "between", "介于", "exists", "有值", "empty", "空值"],
  author_name: [["in", "属于"], ["not_in", "不属于"], ["contains", "包含"], ["not_contains", "不含"], ["regex", "正则"], ["exists", "有值"], ["empty", "空值"]],
  author_mid: [["in", "属于"], ["not_in", "不属于"], ["exists", "有值"], ["empty", "空值"], ["in_list", "在名单"], ["not_in_list", "不在名单"]],
  title: [["contains", "包含"], ["not_contains", "不含"], ["regex", "正则"], ["exists", "有值"], ["empty", "空值"]],
  progress_pct: [">=", "≥", "<=", "≤", ">", ">", "<", "<", "exists", "有值", "empty", "空值"],
  progress_sec: [">=", "≥", "<=", "≤", ">", ">", "<", "<", "exists", "有值", "empty", "空值"],
  view_at_age_days: [">", ">", "<", "<", "between", "介于", ">=", "≥", "<=", "≤", "exists", "有值", "empty", "空值"],
  tag_name: [["contains", "包含"], ["not_contains", "不含"], ["exists", "有值"], ["empty", "空值"]],
  remark: [["contains", "包含"], ["not_contains", "不含"], ["regex", "正则"], ["exists", "有值"], ["empty", "空值"]],
  main_category: [["contains", "包含"], ["in", "属于"], ["not_in", "不属于"], ["exists", "有值"], ["empty", "空值"]],
  is_fav: [["==", "等于"], ["!=", "不等于"], ["exists", "有值"], ["empty", "空值"]],
  live_status: [["==", "等于"], ["!=", "不等于"], ["exists", "有值"], ["empty", "空值"]],
  videos: [">", ">", ">=", "≥", "==", "=", "exists", "有值", "empty", "空值"],
  progress: [">=", "≥", "<=", "≤", ">", ">", "<", "<", "exists", "有值", "empty", "空值"],
  view_at: [">", ">", "<", "<", "exists", "有值", "empty", "空值", ["relative_after", "近N天内"], ["relative_before", "超过N天"]],
  dt: [["==", "等于"], ["!=", "不等于"], ["exists", "有值"], ["empty", "空值"]],
};
const FIELD_TYPE = {
  business: "types", duration: "num", author_name: "text", author_mid: "text",
  progress_pct: "num", progress_sec: "num", view_at_age_days: "num", title: "text",
};
const OP_BY_FIELD = {
  business: [["in", "属于"], ["not_in", "不属于"]],
  duration: [">=", "≥", "<=", "≤", ">", ">", "<", "<", "between", "介于"],
  author_name: [["in", "属于"], ["not_in", "不属于"], ["contains", "包含"]],
  author_mid: [["in", "属于"], ["not_in", "不属于"]],
  progress_pct: [">=", "≥", "<=", "≤", ">", ">", "<", "<"],
  progress_sec: [">=", "≥", "<=", "≤", ">", ">", "<", "<"],
  view_at_age_days: [">", ">", "<", "<", "between", "介于"],
  title: [["contains", "包含"]],
};
const TYPE_OPTIONS = ["archive", "live", "article", "pgc"];

// 数值条件滑块配置：返回 {min,max,step,fmt}；非数值/无需滑块字段返回 null
function sliderSpec(field) {
  switch (field) {
    case "progress_pct": return { min: 0, max: 1, step: 0.01, fmt: (v) => Math.round(v * 100) + "%" };
    case "progress_sec":
    case "progress": return { min: 0, max: 7200, step: 5, fmt: (v) => fmtDur(v) };
    case "duration": return { min: 0, max: 7200, step: 5, fmt: (v) => fmtDur(v) };
    case "view_at_age_days": return { min: 0, max: 365, step: 1, fmt: (v) => (v <= 0 ? "0" : v + " 天") };
    case "videos": return { min: 0, max: 200, step: 1, fmt: (v) => v + " P" };
    case "is_fav":
    case "live_status": return { min: 0, max: 1, step: 1, fmt: (v) => (v ? "1" : "0") };
    case "dt": return { min: 0, max: 10, step: 1, fmt: (v) => "dt=" + v };
    default: return null;
  }
}

function genFieldOptions(sel) {
  return Object.keys(FIELD_LABELS).map(
    (f) => `<option value="${f}" ${f === sel ? "selected" : ""}>${FIELD_LABELS[f]}</option>`
  ).join("");
}
function genOpOptions(field, sel) {
  const ops = OP_BY_FIELD[field] || [];
  return ops.map((o) => {
    const v = Array.isArray(o) ? o[0] : o;
    const t = Array.isArray(o) ? o[1] : o;
    return `<option value="${v}" ${v === sel ? "selected" : ""}>${t}</option>`;
  }).join("");
}
function genValueWidget(field, cond, ri, gi, ci) {
  const t = FIELD_TYPE[field];
  const v = cond.value;
  if (t === "types") {
    const set = (typeof v === "string" && v) ? v.split(",").map((x) => x.trim()).filter(Boolean) : (Array.isArray(v) ? v : []);
    const cbs = TYPE_OPTIONS.map((tp) =>
      `<label class="chk mini"><input type="checkbox" data-ri="${ri}" data-g="${gi}" data-c="${ci}" data-k="value-types" value="${tp}" ${set.includes(tp) ? "checked" : ""}/>${tp}</label>`
    ).join(" ");
    return `<span class="val-types">${cbs}</span>`;
  }
  if (t === "num") {
    const spec = sliderSpec(field);
    const num = (typeof v === "number") ? v : (v != null ? v : 0);
    let slide = "", label = "";
    if (spec) {
      slide = `<input type="range" class="val-slide" data-ri="${ri}" data-g="${gi}" data-c="${ci}" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${num}" />`;
      label = `<span class="val-slide-label" data-ri="${ri}" data-g="${gi}" data-c="${ci}">${spec.fmt(num)}</span>`;
    }
    return `<span class="val-num-wrap">${slide}<input type="number" class="val-num" data-ri="${ri}" data-g="${gi}" data-c="${ci}" data-k="value" value="${escapeHtml(String(num))}" step="any" />${label}</span>`;
  }
  const txt = (typeof v === "string") ? v : (Array.isArray(v) ? v.join(",") : "");
  return `<input type="text" class="val-text" data-ri="${ri}" data-g="${gi}" data-c="${ci}" data-k="value" value="${escapeHtml(txt)}" placeholder="逗号分隔多值" />`;
}

function condHtml(c, ri, gi, ci) {
  return `<div class="rg-cond" data-ri="${ri}" data-g="${gi}" data-c="${ci}">
    <select class="cond-field" data-ri="${ri}" data-g="${gi}" data-c="${ci}">${genFieldOptions(c.field)}</select>
    <select class="cond-op" data-ri="${ri}" data-g="${gi}" data-c="${ci}">${genOpOptions(c.field, c.op)}</select>
    ${genValueWidget(c.field, c, ri, gi, ci)}
    <button class="cond-del btn-ghost btn-sm" data-ri="${ri}" data-g="${gi}" data-c="${ci}">✕</button>
  </div>`;
}

function ruleCardHtml(rule, ri) {
  const groups = (rule.groups || []).map((g, gi) => `
    <div class="rule-group" data-ri="${ri}" data-g="${gi}">
      <div class="rg-head">
        <input type="text" class="rg-label" data-ri="${ri}" data-g="${gi}" value="${escapeHtml(g.label || "")}" placeholder="分组名(如 误触)" />
        <select class="rg-action" data-ri="${ri}" data-g="${gi}">
          <option value="auto_skip" ${g.action === "auto_skip" ? "selected" : ""}>自动跳过</option>
          <option value="stale" ${g.action === "stale" ? "selected" : ""}>搁置</option>
        </select>
        <button class="rg-del btn-ghost btn-sm" data-ri="${ri}" data-g="${gi}">删除分组</button>
      </div>
      <div class="rg-conds">
        ${(g.conditions || []).map((c, ci) => condHtml(c, ri, gi, ci)).join("")}
      </div>
      <button class="cond-add btn-ghost btn-sm" data-ri="${ri}" data-g="${gi}">+ 条件</button>
    </div>`).join("");
  const tag = rule.active
    ? '<span class="rc-tag on">生效中</span>'
    : '<span class="rc-tag">未生效</span>';
  return `<div class="rule-card ${rule.active ? "active" : "inactive"} ${ri === editingIdx ? "expanded" : ""}" data-ri="${ri}">
    <div class="rc-head" data-ri="${ri}">
      <span class="rc-chevron">▸</span>
      <label class="chk"><input type="checkbox" class="rc-active" data-ri="${ri}" ${rule.active ? "checked" : ""}/> 激活</label>
      <span class="rc-name">${escapeHtml(rule.name || ("规则" + (ri + 1)))}</span>
      ${tag}
    </div>
    <div class="rc-body">
      <div class="rule-match">分组关系：
        <select class="ruleMatch" data-ri="${ri}">
          <option value="any" ${rule.match === "any" ? "selected" : ""}>任一分组命中(any)</option>
          <option value="all" ${rule.match === "all" ? "selected" : ""}>全部分组命中(all)</option>
        </select>
      </div>
      ${groups}
      <button class="rg-add btn-ghost btn-sm" data-ri="${ri}">+ 分组</button>
    </div>
  </div>`;
}

function renderRuleList() {
  const root = $("ruleList");
  if (!rulesData || !rulesData.rules.length) {
    root.innerHTML = '<div class="rule-empty">暂无规则，可点下方「新建空白规则」开始</div>';
    attachRuleListEvents();
    updateAddBar();
    return;
  }
  // 生效中的规则排在最前并默认展开；其余（未生效）排在后面，可点开二次调整
  const order = [];
  const ai = rulesData.rules.findIndex((r) => r.active);
  if (ai >= 0) order.push(ai);
  rulesData.rules.forEach((r, i) => { if (i !== ai) order.push(i); });
  root.innerHTML = order.map((i) => ruleCardHtml(rulesData.rules[i], i)).join("");
  attachRuleListEvents();
  updateAddBar();
}

function attachRuleListEvents() {
  const root = $("ruleList");
  // 展开/收起卡片（点头部，避开激活勾选框）
  root.querySelectorAll(".rc-head").forEach((h) => h.addEventListener("click", (e) => {
    if (e.target.closest(".rc-active")) return;
    const ri = +h.dataset.ri;
    editingIdx = ri;
    root.querySelectorAll(".rule-card").forEach((c) =>
      c.classList.toggle("expanded", +c.dataset.ri === ri));
  }));
  // 激活切换（单 active / C6）
  root.querySelectorAll(".rc-active").forEach((cb) => cb.addEventListener("change", (e) => {
    const ri = +cb.dataset.ri;
    if (cb.checked) {
      rulesData.rules.forEach((r, i) => { r.active = (i === ri); });
    } else {
      if (rulesData.rules.filter((r) => r.active).length <= 1) {
        cb.checked = true; toast("至少保留一条激活规则（C6）"); return;
      }
      rulesData.rules[ri].active = false;
    }
    rulesDirty = true; renderRuleList();
  }));
  // 分组关系
  root.querySelectorAll(".ruleMatch").forEach((s) => s.addEventListener("change", (e) => {
    rulesData.rules[+e.target.dataset.ri].match = e.target.value; rulesDirty = true;
  }));
  // 新增分组 / 删除分组
  root.querySelectorAll(".rg-add").forEach((b) => b.addEventListener("click", () => {
    const ri = +b.dataset.ri;
    rulesData.rules[ri].groups.push({ label: "新分组", action: "auto_skip", conditions: [{ field: "progress_pct", op: ">=", value: 0.95 }] });
    rulesDirty = true; editingIdx = ri; renderRuleList();
  }));
  root.querySelectorAll(".rg-del").forEach((b) => b.addEventListener("click", () => {
    const ri = +b.dataset.ri;
    rulesData.rules[ri].groups.splice(+b.dataset.g, 1);
    rulesDirty = true; renderRuleList();
  }));
  // 条件增删
  root.querySelectorAll(".cond-add").forEach((b) => b.addEventListener("click", () => {
    const ri = +b.dataset.ri, gi = +b.dataset.g;
    rulesData.rules[ri].groups[gi].conditions.push({ field: "progress_pct", op: ">=", value: 0.95 });
    rulesDirty = true; renderRuleList();
  }));
  root.querySelectorAll(".cond-del").forEach((b) => b.addEventListener("click", () => {
    const ri = +b.dataset.ri, gi = +b.dataset.g, ci = +b.dataset.c;
    rulesData.rules[ri].groups[gi].conditions.splice(ci, 1);
    rulesDirty = true; renderRuleList();
  }));
  // 分组名 / 动作
  root.querySelectorAll(".rg-label").forEach((el) => el.addEventListener("change", (e) => {
    rulesData.rules[+e.target.dataset.ri].groups[+e.target.dataset.g].label = e.target.value; rulesDirty = true;
  }));
  root.querySelectorAll(".rg-action").forEach((el) => el.addEventListener("change", (e) => {
    rulesData.rules[+e.target.dataset.ri].groups[+e.target.dataset.g].action = e.target.value; rulesDirty = true;
  }));
  // 字段切换：重置 op/value 防类型错配
  root.querySelectorAll(".cond-field").forEach((el) => el.addEventListener("change", (e) => {
    const ri = +e.target.dataset.ri, gi = +e.target.dataset.g, ci = +e.target.dataset.c;
    const c = rulesData.rules[ri].groups[gi].conditions[ci];
    c.field = e.target.value;
    const ops = OP_BY_FIELD[c.field];
    c.op = Array.isArray(ops[0]) ? ops[0][0] : ops[0];
    c.value = (FIELD_TYPE[c.field] === "types") ? "" : (FIELD_TYPE[c.field] === "num" ? 0 : "");
    rulesDirty = true; renderRuleList();
  }));
  root.querySelectorAll(".cond-op").forEach((el) => el.addEventListener("change", (e) => {
    const ri = +e.target.dataset.ri, gi = +e.target.dataset.g, ci = +e.target.dataset.c;
    rulesData.rules[ri].groups[gi].conditions[ci].op = e.target.value; rulesDirty = true;
  }));
  // 数值 / 文本值
  root.querySelectorAll(".val-num, .val-text").forEach((el) => el.addEventListener("change", (e) => {
    const ri = +e.target.dataset.ri, gi = +e.target.dataset.g, ci = +e.target.dataset.c;
    let val = e.target.value;
    if (el.classList.contains("val-num")) val = (val === "" ? 0 : parseFloat(val));
    rulesData.rules[ri].groups[gi].conditions[ci].value = val; rulesDirty = true;
    if (el.classList.contains("val-num")) {
      const slide = root.querySelector(`.val-slide[data-ri="${ri}"][data-g="${gi}"][data-c="${ci}"]`);
      if (slide) slide.value = (typeof val === "number" ? val : 0);
      const lab = root.querySelector(`.val-slide-label[data-ri="${ri}"][data-g="${gi}"][data-c="${ci}"]`);
      const spec = sliderSpec(rulesData.rules[ri].groups[gi].conditions[ci].field);
      if (lab && spec) lab.textContent = spec.fmt(val);
    }
  }));
  // 数值滑块：拖动时同步数字框与标签
  root.querySelectorAll(".val-slide").forEach((el) => el.addEventListener("input", (e) => {
    const ri = +el.dataset.ri, gi = +el.dataset.g, ci = +el.dataset.c;
    const val = parseFloat(el.value);
    const numEl = root.querySelector(`.val-num[data-ri="${ri}"][data-g="${gi}"][data-c="${ci}"]`);
    if (numEl) numEl.value = val;
    const lab = root.querySelector(`.val-slide-label[data-ri="${ri}"][data-g="${gi}"][data-c="${ci}"]`);
    const spec = sliderSpec(rulesData.rules[ri].groups[gi].conditions[ci].field);
    if (lab && spec) lab.textContent = spec.fmt(val);
    rulesData.rules[ri].groups[gi].conditions[ci].value = val; rulesDirty = true;
  }));
  // 类型多选
  root.querySelectorAll(".val-types input[type=checkbox]").forEach((cb) => cb.addEventListener("change", () => {
    const ri = +cb.dataset.ri, gi = +cb.dataset.g, ci = +cb.dataset.c;
    const checked = [...document.querySelectorAll(`.val-types input[data-ri="${ri}"][data-g="${gi}"][data-c="${ci}"]:checked`)].map((x) => x.value);
    rulesData.rules[ri].groups[gi].conditions[ci].value = checked.join(",");
    rulesDirty = true;
  }));
}

/* 类别（类型）词表与「未覆盖类别」预设 */
const TYPE_LABELS = { archive: "视频/投稿", live: "直播", article: "专栏", pgc: "番剧" };
function typeLabel(t) { return TYPE_LABELS[t] || t; }

// 当前生效规则已覆盖的类别（business in [...] 条件列出的类型）
function coveredTypes(rule) {
  const s = new Set();
  (rule.groups || []).forEach((g) => (g.conditions || []).forEach((c) => {
    if (c.field === "business" && c.op === "in") {
      String(c.value || "").split(",").map((x) => x.trim()).filter(Boolean).forEach((t) => s.add(t));
    }
  }));
  return s;
}
// 当前生效规则未覆盖的类别（类型）
function uncoveredTypes() {
  const active = rulesData && rulesData.rules.find((r) => r.active);
  const covered = active ? coveredTypes(active) : new Set();
  return TYPE_OPTIONS.filter((t) => !covered.has(t));
}
// 抽屉底部「新建预设」按钮：根据未覆盖类别启停 + 动态标签
function updateAddBar() {
  const presetBtn = $("addPreset");
  if (!presetBtn) return;
  const unc = uncoveredTypes();
  if (!unc.length) {
    presetBtn.disabled = true;
    presetBtn.title = "当前规则的类别已全覆盖，暂无可加入的预设";
    presetBtn.textContent = "＋ 新建预设";
  } else {
    presetBtn.disabled = false;
    presetBtn.title = "把未覆盖的类别打包成一套规则直接加入：" + unc.map(typeLabel).join("、");
    presetBtn.textContent = "＋ 新建预设 (" + unc.length + ")";
  }
}
function addBlankRule() {
  if (!rulesData) return;
  const blank = { id: "rule_" + Date.now(), name: "新规则", active: false, match: "any", groups: [] };
  rulesData.rules.push(blank);
  editingIdx = rulesData.rules.length - 1;
  rulesDirty = true;
  renderRuleList();
  toast("已新建空白规则（未生效，可在卡片内添加分组/条件并激活）");
}
function addPresetRule() {
  if (!rulesData) return;
  const unc = uncoveredTypes();
  if (!unc.length) { toast("当前规则的类别已全覆盖，暂无可加入的预设"); return; }
  const preset = {
    id: "rule_" + Date.now(),
    name: "预设·" + unc.map(typeLabel).join("/"),
    active: false, match: "any",
    groups: [{ label: "未覆盖类别", action: "auto_skip",
      conditions: [{ field: "business", op: "in", value: unc.join(",") }] }],
  };
  rulesData.rules.push(preset);
  editingIdx = rulesData.rules.length - 1;
  rulesDirty = true;
  renderRuleList();
  toast("已加入预设：" + unc.map(typeLabel).join("、") + "（可继续调整）");
}

/* ====== 规则：加载 / 抽屉开关 / 应用 / 状态角标 ====== */

async function loadRules() {
  try {
    rulesData = await (await fetch("/api/rules")).json();
    editingIdx = rulesData.rules.findIndex((r) => r.active);
    if (editingIdx < 0 && rulesData.rules.length) editingIdx = 0;
    renderRuleList();
  } catch (e) {
    toast("加载规则失败：" + e.message);
  }
}

async function saveRulesCall() {
  const res = await fetch("/api/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rulesData),
  });
  return res.json();
}

function showConflicts(c) {
  const box = $("ruleConflicts");
  if (!box) return;
  if (!c || ((!c.hard || !c.hard.length) && (!c.soft || !c.soft.length))) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  let html = "";
  (c.hard || []).forEach((m) => html += `<div class="conf hard">⛔ ${escapeHtml(m)}</div>`);
  (c.soft || []).forEach((m) => html += `<div class="conf soft">⚠️ ${escapeHtml(m)}</div>`);
  box.innerHTML = html;
  box.classList.remove("hidden");
}

function openRuleDrawer() {
  if (drawerOpen) return;
  drawerOpen = true;
  if (!rulesData) loadRules(); else renderRuleList();
  $("ruleOverlay").classList.remove("hidden");
  const d = $("ruleDrawer");
  d.classList.remove("hidden");
  d.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => d.classList.add("open"));
  document.body.classList.add("lock-scroll");
  // 编辑中：屏蔽同步 / 全量
  $("syncBtn").disabled = true; $("syncBtn").title = "规则编辑中不可用";
  $("fullBtn").disabled = true; $("fullBtn").title = "规则编辑中不可用";
  setTimeout(() => {
    const f = d.querySelector(".rc-active") || d.querySelector("button");
    if (f) f.focus();
  }, 60);
  document.addEventListener("keydown", onDrawerKey);
}

function closeRuleDrawer() {
  if (!drawerOpen) return;
  if (rulesDirty) {
    if (!confirm("有未应用的规则改动，退出将丢弃？")) return;
    rulesDirty = false;
    loadRules();   // 丢弃：从服务端恢复编辑前状态
  }
  drawerOpen = false;
  const d = $("ruleDrawer");
  d.classList.remove("open");
  d.setAttribute("aria-hidden", "true");
  $("ruleOverlay").classList.add("hidden");
  document.body.classList.remove("lock-scroll");
  setTimeout(() => d.classList.add("hidden"), 220);
  $("syncBtn").disabled = false; $("syncBtn").title = "同步数据";
  $("fullBtn").disabled = false; $("fullBtn").title = "强制全量重新拉取";
  document.removeEventListener("keydown", onDrawerKey);
  $("ruleBtn").focus();
}

function onDrawerKey(e) {
  if (e.key === "Escape") { e.preventDefault(); closeRuleDrawer(); return; }
  if (e.key === "Tab") trapFocus(e, $("ruleDrawer"));
}

function trapFocus(e, container) {
  const f = container.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
  const vis = [...f].filter((el) => el.offsetParent !== null && !el.disabled);
  if (!vis.length) return;
  const first = vis[0], last = vis[vis.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

async function pollRuleStatus() {
  try {
    const s = await (await fetch("/api/rules-status")).json();
    const pend = $("rulePending"), dirty = $("ruleDirty");
    pend.classList.toggle("hidden", !s.pending);
    if (s.dirty_count > 0) {
      dirty.textContent = s.dirty_count > 99 ? "99+" : String(s.dirty_count);
      dirty.classList.remove("hidden");
    } else {
      dirty.classList.add("hidden");
    }
  } catch (e) { /* 忽略 */ }
}

$("ruleBtn").addEventListener("click", openRuleDrawer);
$("ruleClose").addEventListener("click", closeRuleDrawer);
$("ruleOverlay").addEventListener("click", closeRuleDrawer);

$("saveRules").addEventListener("click", async () => {
  const sv = await saveRulesCall();
  if (sv.ok) {
    rulesDirty = false;
    showConflicts(null);
    renderRuleList();
    pollRuleStatus();   // 已保存未应用 → 显示『待应用』角标
    if (sv.warnings && sv.warnings.length) toast("已保存（含警告：" + sv.warnings[0] + "）");
    else toast("规则已保存（待应用）");
  } else {
    showConflicts(sv.conflicts);
    toast("规则有冲突，未保存（见下方提示）");
  }
});

let ruleRunning = false;
$("applyRules").addEventListener("click", async () => {
  if (ruleRunning) return;
  const sv = await saveRulesCall();
  if (!sv.ok) {
    showConflicts(sv.conflicts);
    toast("规则有冲突，未应用（见下方提示）");
    return;
  }
  showConflicts(null);
  rulesDirty = false;
  renderRuleList();
  let dry;
  try {
    dry = await (await fetch("/api/rules-dry")).json();
  } catch (e) { toast("规则预览失败：" + e.message); return; }
  if (!dry.ok) { toast("规则应用失败：" + (dry.err || "")); return; }
  const msg = `将标记自动跳过 ${dry.auto_set} 条、搁置 ${dry.stale_set} 条`
    + (dry.manual_cleared ? `（覆盖手动标记 ${dry.manual_cleared} 条）` : "")
    + "。确认应用？";
  if (!confirm(msg)) return;
  const r = await (await fetch("/api/apply-rules", { method: "POST" })).json();
  if (r.blocked) { toast(r.reason || "已有任务进行中"); return; }
  if (r.started) startRulePoll();
});

$("addBlank").addEventListener("click", addBlankRule);
$("addPreset").addEventListener("click", addPresetRule);

function startRulePoll() {
  const prog = $("autoSkipProgress");
  const bar = prog.querySelector(".sync-bar > i");
  const step = $("autoSkipStep");
  prog.classList.remove("hidden");
  $("applyRules").disabled = true;
  ruleRunning = true;
  const tick = () => {
    fetch("/api/auto-skip-status").then((r) => r.json()).then((s) => {
      const p = s.progress || {};
      const denom = p.total || 0;
      let pct = 0;
      if (p.completed) pct = 100;
      else if (denom > 0 && p.done) pct = Math.min(99, Math.round((p.done / denom) * 100));
      bar.style.width = pct + "%";
      step.textContent = p.completed
        ? `规则应用完成：${p.auto_set || 0} 条跳过 / ${p.stale_set || 0} 条搁置`
        : `正在应用规则…（${p.done || 0}/${denom}）`;
      if (s.running) {
        setTimeout(tick, 400);
      } else {
        prog.classList.add("hidden");
        $("applyRules").disabled = false;
        ruleRunning = false;
        if (s.last && s.last.ok) {
          toast(`已应用规则：${s.last.auto_set} 条跳过`
            + (s.last.stale_set ? ` / ${s.last.stale_set} 条搁置` : "")
            + (s.last.manual_cleared ? `（覆盖手动 ${s.last.manual_cleared} 条）` : ""));
          load(true);
          pollRuleStatus();   // 应用后：待应用清除、脏计数归零
        } else if (s.last) {
          toast("规则应用失败：" + (s.last.err || ""));
        }
      }
    }).catch(() => {
      prog.classList.add("hidden");
      $("applyRules").disabled = false;
      ruleRunning = false;
    });
  };
  tick();
}

/* ====== 时长筛选 ====== */
document.querySelectorAll("[data-dur]").forEach((el) => {
  el.addEventListener("click", () => {
    document.querySelectorAll("[data-dur]").forEach((e) => e.classList.remove("active"));
    el.classList.add("active");
    state.dur = el.dataset.dur || "";
    load(true);
  });
});

/* ====== 时间快捷筛选 ====== */
document.querySelectorAll("[data-time]").forEach((el) => {
  el.addEventListener("click", () => {
    document.querySelectorAll("[data-time]").forEach((e) => e.classList.remove("active"));
    el.classList.add("active");
    state.timeRange = el.dataset.time || "";
    $("panelDateFrom").value = "";
    $("panelDateTo").value = "";
    state.dateFrom = ""; state.dateTo = "";
    load(true);
  });
});
$("panelDateFrom").addEventListener("change", () => {
  state.dateFrom = $("panelDateFrom").value
    ? String(Math.floor(new Date($("panelDateFrom").value + "T00:00:00").getTime() / 1000))
    : "";
  if (state.dateFrom) {
    document.querySelectorAll("[data-time]").forEach((e) => e.classList.remove("active"));
    state.timeRange = "";
  }
  load(true);
});
$("panelDateTo").addEventListener("change", () => {
  state.dateTo = $("panelDateTo").value
    ? String(Math.floor(new Date($("panelDateTo").value + "T23:59:59").getTime() / 1000))
    : "";
  if (state.dateTo) {
    document.querySelectorAll("[data-time]").forEach((e) => e.classList.remove("active"));
    state.timeRange = "";
  }
  load(true);
});

/* ====== 设备筛选 ====== */
document.querySelectorAll("[data-dt]").forEach((el) => {
  el.addEventListener("click", () => {
    document.querySelectorAll("[data-dt]").forEach((e) => e.classList.remove("active"));
    el.classList.add("active");
    state.dt = el.dataset.dt || "";
    load(true);
  });
});

/* ====== 左侧时间导航 ====== */
document.querySelectorAll(".side-item").forEach((el) => {
  el.addEventListener("click", () => {
    document.querySelectorAll(".side-item").forEach((e) => e.classList.remove("active"));
    el.classList.add("active");
    const range = el.dataset.range;
    if (range === "today" || range === "yesterday" || range === "week") {
      state.timeRange = range;
      // 关键修复：清除上一次 older/month 留下的 dateTo/dateFrom，
      // 否则 buildQueryBody 里 dateTo 会覆盖 time_to，形成 [today_start, 几周前] 的非法区间 → 全空
      state.dateFrom = ""; state.dateTo = "";
      $("panelDateFrom").value = "";
      $("panelDateTo").value = "";
    } else if (range === "older") {
      // 一周前 = 比 7 天更早（到历史起点）：设 dateTo 上限，清空 dateFrom
      const w = new Date(); w.setDate(w.getDate() - 7);
      state.dateTo = String(Math.floor(new Date(w.getFullYear(), w.getMonth(), w.getDate()).getTime() / 1000));
      state.dateFrom = "";
      state.timeRange = "";
    } else if (range === "month") {
      // 一个月前 = 比 30 天更早（到历史起点）：设 dateTo 上限，清空 dateFrom
      const m = new Date(); m.setDate(m.getDate() - 30);
      state.dateTo = String(Math.floor(new Date(m.getFullYear(), m.getMonth(), m.getDate()).getTime() / 1000));
      state.dateFrom = "";
      state.timeRange = "";
    }
    document.querySelectorAll("[data-time]").forEach((e) => {
      e.classList.toggle("active", e.dataset.time === state.timeRange);
    });
    load(true);
  });
});

/* ====== 搜索 ====== */
let searchTimer = null;
$("q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = $("q").value.trim(); load(true); }, 350);
});

/* ====== 加载更多 ====== */
$("moreBtn").addEventListener("click", () => load(false));

/* ====== 删除 / 手动跳过（就地切换，可反悔）/ 徽标取消 / 自动恢复 ====== */
listEl.addEventListener("click", (e) => {
  const skipToggle = e.target.closest(".skip-toggle");
  if (skipToggle) {
    e.preventDefault();
    const kid = skipToggle.dataset.kid;
    const act = skipToggle.dataset.act;
    const li = skipToggle.closest(".item");
    if (act === "restore-auto") {
      fetch(`/api/skip?kid=${enc(kid)}&kind=auto`, { method: "POST" })
        .then((r) => r.json())
        .then((d) => {
          if (!d.ok) { toast("操作失败：" + (d.error || "")); return; }
          if (li) li.remove();
          toast("已恢复（取消自动跳过）");
          if (batchMode) updateBatchEligibility();
        })
        .catch(() => toast("操作失败"));
      return;
    }
    const val = act === "cancel" ? 0 : 1;
    fetch(`/api/skip?kid=${enc(kid)}&value=${val}`, { method: "POST" })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) { toast("操作失败：" + (d.error || "")); return; }
        if (val) {
          skipToggle.dataset.act = "cancel";
          skipToggle.classList.add("on");
          skipToggle.textContent = "取消跳过";
          if (li) li.dataset.skip = "manual";
          toast("已标记为不需要观看");
        } else {
          skipToggle.dataset.act = "skip";
          skipToggle.classList.remove("on");
          skipToggle.textContent = "不需要看?";
          if (li) li.dataset.skip = "";
          toast("已恢复（需要观看）");
        }
        if (batchMode) updateBatchEligibility();
      })
      .catch(() => toast("操作失败"));
    return;
  }
  const badge = e.target.closest(".badge-skip.cancelable");
  if (badge) {
    e.preventDefault();
    const kid = badge.dataset.kid;
    const li = badge.closest(".item");
    const st = li ? (li.dataset.skip || "") : "";
    const url = st === "auto"
      ? `/api/skip?kid=${enc(kid)}&kind=auto`
      : `/api/skip?kid=${enc(kid)}&value=0`;
    fetch(url, { method: "POST" })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) { toast("取消失败：" + (d.error || "")); return; }
        if (li) li.dataset.skip = "";
        badge.remove();
        toast("已取消跳过");
        if (batchMode) updateBatchEligibility();
      })
      .catch(() => toast("取消失败"));
    return;
  }
  const btn = e.target.closest(".del-btn");
  if (!btn) return;
  e.preventDefault();
  const li = btn.closest(".item");
  if (li) li.remove();
  toast("已隐藏（刷新后恢复）");
});

/* ====== 批量勾选模式 ====== */
function batchEligible(st) {
  if (batchAction === "delete") return true;
  if (batchAction === "skip") return st === "";
  if (state.view === "needs") return st === "manual";
  return st === "auto";
}

function syncBatchKindToView() {
  if (!batchMode) return;
  const sel = $("batchActionSel");
  const tag = $("batchViewTag");
  if (state.view === "needs") {
    sel.innerHTML =
      '<option value="skip">跳过选中</option>' +
      '<option value="restore">恢复选中</option>';
    if (!["skip", "restore"].includes(batchAction)) batchAction = "skip";
    tag.textContent = "视图：需要观看";
    tag.className = "batch-view-tag tag-needs";
  } else if (state.view === "skipped" || state.view === "stale") {
    sel.innerHTML = '<option value="restore">恢复选中（取消自动跳过）</option>';
    batchAction = "restore";
    tag.textContent = state.view === "stale" ? "视图：已搁置" : "视图：已跳过";
    tag.className = "batch-view-tag tag-stale";
  } else {
    sel.innerHTML = '<option value="delete">删除选中（隐藏）</option>';
    batchAction = "delete";
    tag.textContent = "视图：完整历史";
    tag.className = "batch-view-tag tag-all";
  }
  sel.value = batchAction;
  syncBatchButtons();
}

$("batchBtn").addEventListener("click", () => {
  batchMode = !batchMode;
  listEl.classList.toggle("batch-mode", batchMode);
  $("batchBar").classList.toggle("hidden", !batchMode);
  $("batchBtn").classList.toggle("active", batchMode);
  if (batchMode) {
    syncBatchKindToView();
    toast(
      state.view === "needs" ? "批量模式：勾选视频后「跳过」或「恢复」"
        : (state.view === "skipped" || state.view === "stale") ? "批量模式：勾选视频后「恢复」（取消自动跳过）"
        : "批量模式：勾选视频后「删除选中」（隐藏）"
    );
  }
  load(true);
});

function syncBatchButtons() {
  updateBatchEligibility();
}

$("batchActionSel").addEventListener("change", () => {
  batchAction = $("batchActionSel").value;
  updateBatchEligibility();
});

function updateBatchEligibility() {
  const cards = listEl.querySelectorAll(".item");
  let eligible = 0, checked = 0;
  cards.forEach((li) => {
    const cb = li.querySelector(".batch-cb");
    if (!cb) return;
    const st = li.dataset.skip || "";
    const ok = batchEligible(st);
    cb.disabled = !ok;
    li.classList.toggle("batch-disabled", !ok);
    if (ok) eligible++;
    if (ok && cb.checked) checked++;
  });
  const hint = batchAction === "delete"
    ? `删除模式：勾选视频后「删除选中」即可隐藏（共 ${eligible} 条可操作）`
    : batchAction === "skip"
      ? `跳过模式：仅可勾选「未跳过」的视频（共 ${eligible} 条可操作）`
      : `恢复模式：仅可勾选「已${state.view === "needs" ? "手动跳过" : "自动跳过/搁置"}」的视频（共 ${eligible} 条可操作）`;
  $("batchHint").textContent = hint;
  $("batchCount").textContent = checked;
}

listEl.addEventListener("change", (e) => {
  if (e.target.classList.contains("batch-cb")) updateBatchEligibility();
});

$("batchSelectAll").addEventListener("click", () => {
  listEl.querySelectorAll(".batch-cb:not([disabled])").forEach((cb) => { cb.checked = true; });
  updateBatchEligibility();
});

$("batchExit").addEventListener("click", () => {
  batchMode = false;
  listEl.classList.remove("batch-mode");
  $("batchBar").classList.add("hidden");
  $("batchBtn").classList.remove("active");
  load(true);
});

$("batchApply").addEventListener("click", async () => {
  const cbs = [...listEl.querySelectorAll(".batch-cb:not([disabled]):checked")];
  if (cbs.length === 0) { toast("请先勾选视频"); return; }
  $("batchApply").disabled = true;
  if (batchAction === "delete") {
    let n = 0;
    cbs.forEach((cb) => {
      const li = cb.closest(".item");
      if (li) { li.remove(); n++; }
    });
    $("batchApply").disabled = false;
    toast(`已隐藏 ${n} 条（刷新后恢复）`);
    updateBatchEligibility();
    return;
  }
  if (batchAction === "skip") {
    let okN = 0;
    for (const cb of cbs) {
      try {
        const r = await fetch(`/api/skip?kid=${enc(cb.dataset.kid)}&value=1`, { method: "POST" });
        const d = await r.json();
        if (d.ok) okN++;
      } catch (e) { /* 忽略单条失败 */ }
    }
    $("batchApply").disabled = false;
    toast(`已跳过 ${okN} 条`);
    load(true);
    return;
  }
  const kind = state.view === "needs" ? "manual" : "auto";
  let okN = 0;
  for (const cb of cbs) {
    try {
      const url = kind === "manual"
        ? `/api/skip?kid=${enc(cb.dataset.kid)}&value=0`
        : `/api/skip?kid=${enc(cb.dataset.kid)}&kind=auto`;
      const r = await fetch(url, { method: "POST" });
      const d = await r.json();
      if (d.ok) okN++;
    } catch (e) { /* 忽略单条失败 */ }
  }
  $("batchApply").disabled = false;
  toast(`已恢复 ${okN} 条`);
  load(true);
});

/* ====== 同步状态轮询（进度条 + 文字，无 toast 弹窗） ====== */
function fmtDateTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function applySyncStatus(s) {
  if (!s) return;
  const progressEl = $("syncProgress");
  const barEl = progressEl.querySelector(".sync-bar > i");
  const doneEl = $("syncDone");
  const stepEl = $("syncStep");
  const metaEl = $("syncMeta");

  const pr = s.progress || {};
  const meta = s.meta || {};
  const denom = pr.total_estimate || meta.total || 0;
  let pct = 0;
  if (pr.is_end || pr.completed) {
    pct = 100;
  } else if (denom > 0 && (pr.fetched || 0) > 0) {
    pct = Math.min(99, Math.round((pr.fetched / denom) * 100));
  }

  if (s.running) {
    progressEl.classList.remove("hidden");
    doneEl.classList.add("hidden");
    barEl.style.width = pct + "%";
    const modeText =
      pr.mode === "full" ? "正在全量建基线" :
      pr.mode === "incremental" ? "正在增量同步" :
      pr.mode === "retry" ? "同步中断，正在重试" :
      "正在同步历史记录";
    stepEl.textContent =
      `${modeText}…（已拉取 ${pr.fetched || 0} 条 / 第 ${pr.page || 0} 页，进度 ${pct}%）`;
  } else {
    if (s.last && s.last.ok) {
      progressEl.classList.add("hidden");
      doneEl.className = "sync-done";
      doneEl.classList.remove("hidden");
      const m = s.last.mode === "incremental" ? "，增量"
        : (s.last.mode === "full" ? "，全量" : "");
      doneEl.textContent = (s.last.fetched != null)
        ? `✓ 同步完成（${s.last.fetched} 条${m}）`
        : "✓ 同步完成";
      pollRuleStatus();   // 同步后：更新『脏』角标（可能有新记录命中规则）
    } else if (s.last && s.last.plan_mode === "skip") {
      // 阶段 4：`skip` 是**策略有意跳过**（如刚跑过全量、冷却中），**不是失败** —— 后端
      // `_note_plan_not_started()` 对 blocked / skip 都会写 `ok:false`，故必须靠 `plan_mode`
      // 区分，否则「有意跳过」会被渲染成红色失败（`方案.md` §4 阶段 4 的既有子项）。
      progressEl.classList.add("hidden");
      doneEl.className = "sync-done sync-skip";
      doneEl.classList.remove("hidden");
      doneEl.textContent = "• 本次跳过同步：" + (s.last.err || "策略判定无需执行");
    } else if (s.last && s.last.plan_mode === "blocked") {
      // 阻断（凭证闸门 R5）—— 是「没开始」，得把原因讲清楚而不是笼统的「未完成」
      progressEl.classList.add("hidden");
      doneEl.className = "sync-done sync-fail";
      doneEl.classList.remove("hidden");
      doneEl.textContent = "⚠ 同步被阻断：" + (s.last.err || "当前不可执行，请检查设置");
    } else if (s.last && !s.last.ok) {
      progressEl.classList.add("hidden");
      doneEl.className = "sync-done sync-fail";
      doneEl.classList.remove("hidden");
      doneEl.textContent = "⚠ 同步未完成：" + (s.last.err || "请重试");
    } else {
      progressEl.classList.add("hidden");
      doneEl.classList.add("hidden");
    }
  }

  const ver = (meta.version != null && meta.version !== "") ? meta.version : "—";
  const ts = meta.last_success_at ? fmtDateTime(meta.last_success_at) : "—";
  const total = (meta.total != null) ? meta.total : 0;
  metaEl.innerHTML = `数据 v${ver} · 共 ${total} 条 · 更新于 ${ts}`;
}

function pollSync() {
  fetch("/api/sync").then((r) => r.json()).then(applySyncStatus).catch(() => {});
}

$("syncBtn").addEventListener("click", () => {
  fetch("/api/sync", { method: "POST" }).then(() => { pollSync(); });
});
$("fullBtn").addEventListener("click", () => {
  fetch("/api/sync?full=1", { method: "POST" })
    .then(() => toast("已触发全量重建：将重新校准存档标记并刷新封面"))
    .then(() => pollSync());
});

/* ====== Analyzer 实时更新（Phase 1.5：经 server.py 转发到 Fetcher 8899） ====== */
function applyFetcherHealth(s) {
  const dot = $("fetcherDot");
  if (!dot) return;
  // 阶段 4：判据由 `s.reachable`（传输层）改为**业务级** `analyzer.ok`
  // —— 后端已把 `reachable` 的顶层值换成业务级（见 `refreshSourceHealth` 的注释），
  // 这里读它即可；但若传进来的原始响应带 `connection`，优先取 `connection.analyzer.ok`。
  const ana = (s && s.connection && s.connection.analyzer) || {};
  const ok = (ana.ok !== undefined) ? !!ana.ok : !!(s && s.reachable);
  dot.classList.toggle("on", ok);
  dot.classList.toggle("off", !ok);
  dot.title = ok ? "Analyzer 已连接" : ("Analyzer 不可用：" + ((s && s.error) || (ana.error) || "8899 探测未通过"));
}
function checkFetcher() {
  // ?sessdata=0：这里只管「连通性」，不触发 Analyzer 去调 B站 /login/check（省一次外网往返）。
  // 凭证健康由 refreshSourceHealth() 低频单独取，见文末「数据源健康横幅」。
  fetch("/api/fetcher-health?sessdata=0").then((r) => r.json()).then(applyFetcherHealth).catch(() => {});
}
$("fetcherBtn").addEventListener("click", () => {
  const btn = $("fetcherBtn");
  btn.disabled = true;
  toast("正在触发 Analyzer 重新分析…");
  fetch("/api/fetcher-trigger")
    .then((r) => r.json())
    .then((s) => {
      if (s && s.ok) {
        toast("已触发 Analyzer 重新分析，稍后自动刷新数据");
        // Analyzer 重新拉取需要时间，3 秒后刷新列表（桩/真实后端均适用）
        setTimeout(() => load(true), 3000);
      } else {
        toast("实时更新失败：" + ((s && s.error) || "未知错误"));
      }
    })
    .catch((e) => toast("实时更新失败：" + e.message))
    .finally(() => {
      btn.disabled = false;
      checkFetcher();
    });
});
// 初始检测 Analyzer 连接状态（不阻塞页面）
checkFetcher();

/* ====== Analyzer 联调：逐一测试 Analyzer 接口（需本机 Analyzer 后台运行） ====== */
function showDiag(title, payload) {
  $("diagTitle").textContent = title;
  const el = $("diagBody");
  if (typeof payload === "string") el.textContent = payload;
  else { try { el.textContent = JSON.stringify(payload, null, 2); } catch (e) { el.textContent = String(payload); } }
  $("diagModal").classList.remove("hidden");
}
$("diagClose").addEventListener("click", () => $("diagModal").classList.add("hidden"));
$("diagModal").addEventListener("click", (e) => { if (e.target === $("diagModal")) $("diagModal").classList.add("hidden"); });

function anCall(btn, url, title) {
  // 阶段 4：置灰的按钮**不发起请求**（点了必失败不如直接给提示）——
  // `cap-disabled` 的元素在 gray 模式下只是变灰，浏览器不会阻止 click，故要显式拦。
  if (btn && btn.classList.contains("cap-disabled")) {
    toast(btn.title || "当前能力不可用");
    return;
  }
  if (btn) btn.disabled = true;
  toast("请求 Analyzer：" + title + " …");
  fetch(url)
    .then((r) => r.json().then((body) => ({ status: r.status, body })))
    .then(({ status, body: s }) => {
      // 阶段 4（T10）：409 走统一消费 —— 弹出后端给的原因，并把对应入口置灰
      if (status === 409) { handleApiError(409, s); showDiag(title + " — 不可用", s); return; }
      showDiag(title + " — 响应", s);
      checkFetcher();
      if (title.indexOf("拉取") >= 0) setTimeout(() => load(true), 3000);
    })
    .catch((e) => showDiag(title + " — 错误", { error: e.message }))
    .finally(() => { if (btn) btn.disabled = false; });
}
$("anHealthBtn").addEventListener("click", () => anCall($("anHealthBtn"), "/api/fetcher-health", "① 健康探测 (/health)"));
$("anRealtimeBtn").addEventListener("click", () => anCall($("anRealtimeBtn"), "/api/fetcher-trigger", "② 增量拉取 (/fetch/bili-history-realtime)"));
$("anFullBtn").addEventListener("click", () => anCall($("anFullBtn"), "/api/fetcher-trigger?mode=full", "③ 全量拉取 (/fetch/bili-history)"));
$("anDataBtn").addEventListener("click", () => {
  const btn = $("anDataBtn");
  btn.disabled = true;
  toast("正在执行数据自检（调用 Analyzer 完整性校验）…");
  fetch("/api/fetcher-check")
    .then((r) => r.json())
    .then(showSelfCheck)
    .catch((e) => showDiag("④ 数据自检 — 错误", { error: e.message }))
    .finally(() => { btn.disabled = false; });
});

function showSelfCheck(s) {
  if (!s || s.ok === false) {
    showDiag("④ 数据自检 — 失败", s || { error: "空响应" });
    return;
  }
  const c = s.check || {};
  const f = (x) => (x === undefined || x === null ? "-" : x);
  let t = "";
  t += "④ 数据自检结果\n";
  t += "────────────────────────\n";
  t += "Analyzer 可达: 是 (health_status=" + s.health_status + ")\n";
  t += "Analyzer 主源条数: " + s.records_read + "\n";
  t += "Finder 本地备份源: " + s.local_backup + " 条\n\n";
  if (c.check_error) {
    t += "完整性校验调用失败: " + c.check_error + "\n";
  } else {
    t += "完整性校验 (POST /data_sync/check → Analyzer):\n";
    t += "  JSON 文件数:      " + f(c.total_json_files) + "\n";
    t += "  JSON 记录数:      " + f(c.total_json_records) + "\n";
    t += "  DB 记录数:        " + f(c.total_db_records) + "\n";
    t += "  缺失 (missing):   " + f(c.missing_records_count) + "\n";
    t += "  多余 (extra):     " + f(c.extra_records_count) + "\n";
    t += "  差异 (difference): " + f(c.difference) + "\n";
    t += "  结果文件: " + (c.result_file || "-") + "\n";
    t += "  报告文件: " + (c.report_file || "-") + "\n";
    if (c.report) {
      t += "\n──────── 完整性报告 (markdown) ────────\n";
      t += c.report + "\n";
    }
  }
  showDiag("④ 数据自检", t);
}

$("anBackupBtn").addEventListener("click", () => {
  const btn = $("anBackupBtn");
  btn.disabled = true;
  toast("正在生成本地备份快照…");
  fetch("/api/backup", { method: "POST" })
    .then((r) => r.json())
    .then((s) => {
      showDiag("⑤ 本地备份 — 结果", s && s.manifest ? s.manifest : s);
      checkFetcher();
    })
    .catch((e) => showDiag("⑤ 本地备份 — 错误", { error: e.message }))
    .finally(() => { btn.disabled = false; });
});

$("anBackupListBtn").addEventListener("click", () => {
  fetch("/api/backups")
    .then((r) => r.json())
    .then((s) => showDiag("本地备份列表 (" + (s.backups ? s.backups.length : 0) + ")", s && s.backups ? s.backups : s))
    .catch((e) => showDiag("查看备份 — 错误", { error: e.message }));
});

/* ====== ⑥ 应用变更：唯一入口，软件自动判定 热重载 / 重启 / 只需刷新 ====== */
const CODE_POLL_MS = 15000;

function fmtChanges(ch) {
  ch = ch || {};
  const f = (k, lab) => ((ch[k] || []).length ? lab + "：" + ch[k].join("、") : "");
  return [f("restart", "需重启"), f("reload", "可热重载"), f("static", "前端静态")]
    .filter(Boolean).join("\n") || "（无）";
}

function codeBadgeSet(n, action) {
  const b = $("codeBadge");
  if (!b) return;
  b.dataset.action = action || "";
  if (!n) { b.classList.add("hidden"); b.textContent = "0"; b.title = ""; return; }
  b.classList.remove("hidden");
  b.textContent = n;
  b.title = "有 " + n + " 处变更待应用 · 建议动作：" + (action || "-");
}

async function refreshCodeStatus() {
  try {
    const s = await fetch("/api/code-status", { cache: "no-store" }).then((r) => r.json());
    codeBadgeSet(s.pending || 0, s.next_action);
    const btn = $("anApplyBtn");
    if (btn) {
      btn.title = "POST /api/apply：唯一入口，自动判定热重载 / 重启 / 只需刷新。\n"
        + "当前待应用 " + (s.pending || 0) + " 处（建议：" + s.next_action + "）\n"
        + "boot_id=" + s.boot_id + "  version=" + s.version + "  pid=" + s.pid
        + "  supervised=" + (s.supervised ? "yes" : "NO")
        + "\n护栏：" + s.restart_guard.recent + "/" + s.restart_guard.limit
        + " 次（" + s.restart_guard.window_s + "s 窗口）";
    }
    return s;
  } catch (e) { return null; }
}

$("anApplyBtn").addEventListener("click", async () => {
  const btn = $("anApplyBtn");
  btn.disabled = true;
  try {
    const pre = await refreshCodeStatus();
    const st = await fetch("/api/apply", { method: "POST" }).then((r) => r.json());
    const a = st.action;

    if (a === "none") {
      const c = (pre && pre.console) || {};
      showDiag("⑥ 应用变更", "未检测到任何变更，无需操作。\n\n"
        + "boot_id=" + st.boot_id + "   version=" + st.version + "   pid=" + st.pid
        + "\n控制台：" + (c.note || "未探测"));
      return;
    }

    if (a === "blocked") {
      showDiag("⑥ 应用变更 — 已拒绝重启",
        (st.blocked_reason || "") + "\n\n变更清单：\n" + fmtChanges(st.changes));
      return;
    }

    if (a === "manual") {
      showDiag("⑥ 应用变更 — 需手动重启",
        (st.warning || "") + "\n\n变更清单：\n" + fmtChanges(st.changes)
        + (st.partial_reload ? "\n\n已热重载部分：" + JSON.stringify(st.partial_reload) : ""));
      return;
    }

    if (a === "static") {
      showDiag("⑥ 应用变更 — 只需刷新浏览器",
        (st.hint || "") + "\n\n变更文件：\n" + fmtChanges(st.changes));
      return;
    }

    if (a === "restart") {
      toast("正在重启服务…");
      const oldBoot = pre && pre.boot_id;
      const pc = (pre && pre.console) || {};
      showDiag("⑥ 应用变更 — 已自动重启",
        "检测到需重启进程的改动：\n" + (st.changes.restart || []).join("、")
        + "\n\n已发出重启请求，start.bat 的 supervisor 会在约 2 秒后重新拉起；"
        + "服务恢复后本页会自动刷新。"
        + "\n控制台：" + (pc.note || "未探测"));
      let newBoot = null;
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 500));
        try {
          const s = await fetch("/api/code-status", { cache: "no-store" }).then((r) => r.json());
          if (s && s.boot_id && s.boot_id !== oldBoot) { newBoot = s; break; }
        } catch (e) { /* 重启间隙，继续等待 */ }
      }
      if (newBoot) { location.reload(); return; }
      showDiag("⑥ 应用变更 — 等待超时",
        "20 秒内未等到新进程（boot_id 未变化）。\n\n"
        + "① 最可能：旧进程没能退出 —— Windows 控制台被鼠标「选中」时，向它的任何输出都会"
        + "被冻结，重启链就卡在那一步。\n"
        + "   → 到那个控制台窗口按一下 Esc 或回车，重启会立刻继续。\n"
        + "   → 本机控制台自检：" + (pc.attached
              ? ("快速编辑(QuickEdit) " + (pc.quick_edit ? "开启 ⚠️（选中即冻结）" : "已关闭 ✓"))
              : "未连接控制台")
        + "\n   → 本版已做防护：服务端输出全部改为非阻塞、重启链只写文件、3 秒看门狗兜底；"
        + "请先把控制台解冻，再点一次 ⑥ 让它加载。\n\n"
        + "② 若通过 start.bat 启动：看那个窗口里的报错。\n"
        + "③ 若未托管（BHF_SUPERVISED=1 未设置）：请手动运行 start.bat。\n"
        + "④ 120 秒内重启已达 3 次时，护栏会拒绝后续重启。\n\n"
        + "重启链路留痕：data/run/restart.log");
      btn.disabled = false;
      return;
    }

    /* a === "reload"：已热重载数据层 / 规则 */
    const r = st.reload || {};
    let t = "";
    t += "⑥ 应用变更 → 自动热重载" + (r.ok ? "成功" : "失败")
      + "   耗时 " + (r.elapsed_ms === undefined ? "-" : r.elapsed_ms) + " ms\n";
    t += "────────────────────────\n";
    t += "变更文件：\n" + fmtChanges(st.changes) + "\n";
    t += "重载后记录数: " + (r.records === undefined ? "-" : r.records) + "\n";
    if (st.warning) t += "\n⚠️ " + st.warning + "\n";
    t += "\n" + (st.hint || "");
    showDiag("⑥ 应用变更", t);
    load(true);
    checkFetcher();
    refreshCodeStatus();
  } catch (e) {
    showDiag("⑥ 应用变更 — 错误", { error: e.message });
  } finally {
    btn.disabled = false;
  }
});

/* 徽章：页面加载 / 窗口获得焦点 / 每 15s 轮询一次（只提示，绝不自动执行） */
refreshCodeStatus();
setInterval(() => { if (!document.hidden) refreshCodeStatus(); }, CODE_POLL_MS);
window.addEventListener("focus", () => refreshCodeStatus());

/* ====== 设置：Analyzer / Fetcher 连接（低调入口，非常用触发） ====== */
function openSettings() {
  loadSourceCfg();
  fetch("/api/fetcher-config").then((r) => r.json()).then((c) => {
    $("fetcherBase").value = c.base || "http://localhost:8899";
    $("fetcherKey").value = c.has_key ? "************" : "";
    $("fetcherCfgStatus").textContent = "";
    $("fetcherCfgStatus").className = "fld-status";
    $("settingsModal").classList.remove("hidden");
  }).catch(() => $("settingsModal").classList.remove("hidden"));
}
function closeSettings() {
  $("settingsModal").classList.add("hidden");
}
$("settingsBtn").addEventListener("click", openSettings);
$("settingsClose").addEventListener("click", closeSettings);
$("settingsModal").addEventListener("click", (e) => {
  if (e.target === $("settingsModal")) closeSettings();
});
$("fetcherTest").addEventListener("click", () => {
  const st = $("fetcherCfgStatus");
  st.textContent = "正在测试连接…";
  st.className = "fld-status";
  fetch("/api/fetcher-health").then((r) => r.json()).then((s) => {
    if (s && s.ok && s.reachable) {
      st.textContent = "● 连接正常";
      st.className = "fld-status ok";
    } else {
      st.textContent = "● 不可达：" + ((s && s.error) || "8899 未运行");
      st.className = "fld-status bad";
    }
  }).catch((e) => { st.textContent = "● 测试失败：" + e.message; st.className = "fld-status bad"; });
});
$("fetcherSave").addEventListener("click", () => {
  const base = $("fetcherBase").value.trim();
  // 若输入框是被占位成 ************，说明用户未改 key，发送空串让服务端保留原值
  const keyRaw = $("fetcherKey").value;
  const key = keyRaw === "************" ? "" : keyRaw;
  const st = $("fetcherCfgStatus");
  st.textContent = "正在保存…";
  st.className = "fld-status";
  fetch("/api/fetcher-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base, key }),
  }).then((r) => r.json()).then((s) => {
    if (s && s.ok) {
      st.textContent = "● 已保存" + (s.health && s.health.ok ? "，连接正常" : "，但当前不可达");
      st.className = "fld-status ok";
      checkFetcher();
    } else {
      st.textContent = "● " + ((s && s.error) || "保存失败");
      st.className = "fld-status bad";
    }
  }).catch((e) => { st.textContent = "● 保存失败：" + e.message; st.className = "fld-status bad"; });
});

setInterval(pollSync, 3000);
pollSync();

/* ====== 初始加载 ====== */
loadRules();
load(true);
pollRuleStatus();

/* ====== 高级筛选器（当前引擎规则的超集：读时查询层） ====== */
let advDrawerOpen = false;

// 高级筛选字段/算子控件生成（复用规则引擎同款 UI 思路，但字段更全）
function advGenFieldOptions(sel) {
  return Object.keys(ADV_FIELD_LABELS).map(
    (f) => `<option value="${f}" ${f === sel ? "selected" : ""}>${ADV_FIELD_LABELS[f]}</option>`
  ).join("");
}
function advGenOpOptions(field, sel) {
  const ops = ADV_OP_BY_FIELD[field] || [];
  return ops.map((o) => {
    const v = Array.isArray(o) ? o[0] : o;
    const t = Array.isArray(o) ? o[1] : o;
    return `<option value="${v}" ${v === sel ? "selected" : ""}>${t}</option>`;
  }).join("");
}
function advGenValueWidget(field, cond, gi, ci) {
  const t = ADV_FIELD_TYPE[field];
  const v = cond.value;
  const noValOps = ["exists", "empty"];
  if (noValOps.includes(cond.op)) {
    return `<span class="adv-val-none">—</span>`;
  }
  if (cond.op === "in_list" || cond.op === "not_in_list") {
    const lists = window.__lists || [];
    const opts = lists.map((l) => `<option value="${l.id}" ${(v === l.id) ? "selected" : ""}>${escapeHtml(l.name)} (${l.kind === "whitelist" ? "白" : "黑"})</option>`).join("");
    return `<select class="adv-val-list" data-g="${gi}" data-c="${ci}"><option value="">选择名单</option>${opts}</select>`;
  }
  if (cond.op === "relative_after" || cond.op === "relative_before") {
    return `<input type="text" class="adv-val-text" data-g="${gi}" data-c="${ci}" value="${escapeHtml(String(v == null ? "" : v))}" placeholder="如 7d / 30d / 2w" />`;
  }
  if (t === "types") {
    const set = (typeof v === "string" && v) ? v.split(",").map((x) => x.trim()).filter(Boolean) : (Array.isArray(v) ? v : []);
    const cbs = TYPE_OPTIONS.map((tp) =>
      `<label class="chk mini"><input type="checkbox" data-g="${gi}" data-c="${ci}" data-k="value-types" value="${tp}" ${set.includes(tp) ? "checked" : ""}/>${tp}</label>`
    ).join(" ");
    return `<span class="val-types">${cbs}</span>`;
  }
  if (t === "num") {
    const spec = sliderSpec(field);
    const num = (typeof v === "number") ? v : (v != null ? v : 0);
    let slide = "", label = "";
    if (spec) {
      slide = `<input type="range" class="adv-val-slide" data-g="${gi}" data-c="${ci}" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${num}" />`;
      label = `<span class="adv-val-slide-label" data-g="${gi}" data-c="${ci}">${spec.fmt(num)}</span>`;
    }
    return `<span class="adv-val-num-wrap">${slide}<input type="number" class="adv-val-num" data-g="${gi}" data-c="${ci}" value="${escapeHtml(String(num))}" step="any" />${label}</span>`;
  }
  const txt = (typeof v === "string") ? v : (Array.isArray(v) ? v.join(",") : "");
  return `<input type="text" class="adv-val-text" data-g="${gi}" data-c="${ci}" value="${escapeHtml(txt)}" placeholder="文本/正则" />`;
}

function advCondHtml(c, gi, ci) {
  return `<div class="adv-cond" data-g="${gi}" data-c="${ci}">
    <select class="adv-cond-field" data-g="${gi}" data-c="${ci}">${advGenFieldOptions(c.field)}</select>
    <select class="adv-cond-op" data-g="${gi}" data-c="${ci}">${advGenOpOptions(c.field, c.op)}</select>
    ${advGenValueWidget(c.field, c, gi, ci)}
    <button class="adv-cond-del btn-ghost btn-sm" data-g="${gi}" data-c="${ci}">✕</button>
  </div>`;
}

function advGroupHtml(g, gi) {
  return `<div class="adv-group" data-g="${gi}">
    <div class="adv-group-head">
      <select class="adv-group-logic" data-g="${gi}">
        <option value="AND" ${g.logic === "AND" ? "selected" : ""}>组内 AND</option>
        <option value="OR" ${g.logic === "OR" ? "selected" : ""}>组内 OR</option>
      </select>
      <button class="adv-group-del btn-ghost btn-sm" data-g="${gi}">删除分组</button>
    </div>
    <div class="adv-group-conds">
      ${(g.conditions || []).map((c, ci) => advCondHtml(c, gi, ci)).join("")}
    </div>
    <button class="adv-cond-add btn-ghost btn-sm" data-g="${gi}">＋ 条件</button>
  </div>`;
}

function renderAdvGroups() {
  const root = $("advGroups");
  if (!advFilter.groups.length) {
    root.innerHTML = '<div class="adv-empty">暂无条件分组，点下方「＋ 条件分组」添加</div>';
    return;
  }
  root.innerHTML = advFilter.groups.map((g, gi) => advGroupHtml(g, gi)).join("");
  attachAdvGroupEvents();
}

function attachAdvGroupEvents() {
  const root = $("advGroups");
  root.querySelectorAll(".adv-group-logic").forEach((s) => s.addEventListener("change", (e) => {
    advFilter.groups[+e.target.dataset.g].logic = e.target.value;
  }));
  root.querySelectorAll(".adv-group-del").forEach((b) => b.addEventListener("click", () => {
    advFilter.groups.splice(+b.dataset.g, 1); renderAdvGroups();
  }));
  root.querySelectorAll(".adv-cond-add").forEach((b) => b.addEventListener("click", () => {
    const gi = +b.dataset.g;
    advFilter.groups[gi].conditions.push({ field: "progress_pct", op: ">=", value: 0.95 });
    renderAdvGroups();
  }));
  root.querySelectorAll(".adv-cond-del").forEach((b) => b.addEventListener("click", () => {
    const gi = +b.dataset.g, ci = +b.dataset.c;
    advFilter.groups[gi].conditions.splice(ci, 1); renderAdvGroups();
  }));
  root.querySelectorAll(".adv-cond-field").forEach((el) => el.addEventListener("change", (e) => {
    const gi = +e.target.dataset.g, ci = +e.target.dataset.c;
    const c = advFilter.groups[gi].conditions[ci];
    c.field = e.target.value;
    const ops = ADV_OP_BY_FIELD[c.field];
    c.op = Array.isArray(ops[0]) ? ops[0][0] : ops[0];
    c.value = (ADV_FIELD_TYPE[c.field] === "types") ? "" : (ADV_FIELD_TYPE[c.field] === "num" ? 0 : "");
    renderAdvGroups();
  }));
  root.querySelectorAll(".adv-cond-op").forEach((el) => el.addEventListener("change", (e) => {
    const gi = +e.target.dataset.g, ci = +e.target.dataset.c;
    advFilter.groups[gi].conditions[ci].op = e.target.value;
    renderAdvGroups();
  }));
  root.querySelectorAll(".adv-val-num, .adv-val-text").forEach((el) => el.addEventListener("change", (e) => {
    const gi = +el.dataset.g, ci = +el.dataset.c;
    let val = e.target.value;
    if (el.classList.contains("adv-val-num")) val = (val === "" ? 0 : parseFloat(val));
    advFilter.groups[gi].conditions[ci].value = val;
    if (el.classList.contains("adv-val-num")) {
      const slide = root.querySelector(`.adv-val-slide[data-g="${gi}"][data-c="${ci}"]`);
      if (slide) slide.value = (typeof val === "number" ? val : 0);
      const lab = root.querySelector(`.adv-val-slide-label[data-g="${gi}"][data-c="${ci}"]`);
      const spec = sliderSpec(advFilter.groups[gi].conditions[ci].field);
      if (lab && spec) lab.textContent = spec.fmt(val);
    }
  }));
  root.querySelectorAll(".adv-val-slide").forEach((el) => el.addEventListener("input", (e) => {
    const gi = +el.dataset.g, ci = +el.dataset.c;
    const val = parseFloat(el.value);
    const numEl = root.querySelector(`.adv-val-num[data-g="${gi}"][data-c="${ci}"]`);
    if (numEl) numEl.value = val;
    const lab = root.querySelector(`.adv-val-slide-label[data-g="${gi}"][data-c="${ci}"]`);
    const spec = sliderSpec(advFilter.groups[gi].conditions[ci].field);
    if (lab && spec) lab.textContent = spec.fmt(val);
    advFilter.groups[gi].conditions[ci].value = val;
  }));
  root.querySelectorAll(".adv-val-list").forEach((el) => el.addEventListener("change", (e) => {
    const gi = +el.dataset.g, ci = +el.dataset.c;
    advFilter.groups[gi].conditions[ci].value = e.target.value;
  }));
  root.querySelectorAll(".val-types input[type=checkbox]").forEach((cb) => cb.addEventListener("change", () => {
    const gi = +cb.dataset.g, ci = +cb.dataset.c;
    const checked = [...document.querySelectorAll(`.val-types input[data-g="${gi}"][data-c="${ci}"]:checked`)].map((x) => x.value);
    advFilter.groups[gi].conditions[ci].value = checked.join(",");
  }));
}

function renderAdvHaving() {
  const root = $("advHaving");
  if (!advFilter.having) {
    root.innerHTML = '<div class="adv-empty">未设置聚合条件</div>';
    return;
  }
  const h = advFilter.having;
  root.innerHTML = `<div class="adv-having-row">
    <select class="adv-having-field">
      <option value="author_mid" ${h.groupBy === "author_mid" ? "selected" : ""}>同UP主</option>
      <option value="business" ${h.groupBy === "business" ? "selected" : ""}>同类型</option>
      <option value="main_category" ${h.groupBy === "main_category" ? "selected" : ""}>同分区</option>
    </select>
    <select class="adv-having-op">
      <option value=">" ${h.op === ">" ? "selected" : ""}>></option>
      <option value=">=" ${h.op === ">=" ? "selected" : ""}>≥</option>
      <option value="<" ${h.op === "<" ? "selected" : ""}>></option>
      <option value="<=" ${h.op === "<=" ? "selected" : ""}>≤</option>
      <option value="==" ${h.op === "==" ? "selected" : ""}>=</option>
    </select>
    <input type="number" class="adv-having-val" value="${escapeHtml(String(h.value != null ? h.value : 3))}" />
    <button class="adv-having-del btn-ghost btn-sm">✕</button>
  </div>`;
  root.querySelector(".adv-having-field").addEventListener("change", (e) => { advFilter.having.groupBy = e.target.value; });
  root.querySelector(".adv-having-op").addEventListener("change", (e) => { advFilter.having.op = e.target.value; });
  root.querySelector(".adv-having-val").addEventListener("change", (e) => { advFilter.having.value = parseFloat(e.target.value) || 0; });
  root.querySelector(".adv-having-del").addEventListener("click", () => { advFilter.having = null; renderAdvHaving(); });
}

function renderAdvSort() {
  const root = $("advSort");
  if (!sortFields.length) {
    root.innerHTML = '<div class="adv-empty">未设置排序（默认按观看时间倒序）</div>';
    return;
  }
  root.innerHTML = sortFields.map((s, i) => `<div class="adv-sort-row" data-i="${i}">
    <select class="adv-sort-field" data-i="${i}">
      <option value="view_at" ${s.field === "view_at" ? "selected" : ""}>观看时间</option>
      <option value="duration" ${s.field === "duration" ? "selected" : ""}>时长</option>
      <option value="progress" ${s.field === "progress" ? "selected" : ""}>进度(秒)</option>
      <option value="progress_pct" ${s.field === "progress_pct" ? "selected" : ""}>进度比例</option>
      <option value="progress_sec" ${s.field === "progress_sec" ? "selected" : ""}>已看秒数</option>
      <option value="view_at_age_days" ${s.field === "view_at_age_days" ? "selected" : ""}>距今天数</option>
      <option value="title" ${s.field === "title" ? "selected" : ""}>标题</option>
      <option value="author_name" ${s.field === "author_name" ? "selected" : ""}>UP主</option>
      <option value="business" ${s.field === "business" ? "selected" : ""}>类型</option>
      <option value="main_category" ${s.field === "main_category" ? "selected" : ""}>分区</option>
    </select>
    <select class="adv-sort-dir" data-i="${i}">
      <option value="desc" ${s.dir === "desc" ? "selected" : ""}>降序</option>
      <option value="asc" ${s.dir === "asc" ? "selected" : ""}>升序</option>
    </select>
    <button class="adv-sort-del btn-ghost btn-sm" data-i="${i}">✕</button>
  </div>`).join("");
  root.querySelectorAll(".adv-sort-field").forEach((el) => el.addEventListener("change", (e) => { sortFields[+e.target.dataset.i].field = e.target.value; }));
  root.querySelectorAll(".adv-sort-dir").forEach((el) => el.addEventListener("change", (e) => { sortFields[+e.target.dataset.i].dir = e.target.value; }));
  root.querySelectorAll(".adv-sort-del").forEach((b) => b.addEventListener("click", () => { sortFields.splice(+b.dataset.i, 1); renderAdvSort(); }));
}

function renderAdvViews() {
  const root = $("advViews");
  const views = window.__views || [];
  if (!views.length) {
    root.innerHTML = '<div class="adv-empty">暂无保存的视图</div>';
    return;
  }
  root.innerHTML = views.map((v) => `<div class="adv-view-item ${v.id === activeViewId ? "active" : ""}" data-id="${escapeHtml(v.id)}">
    <span class="adv-view-name" data-id="${escapeHtml(v.id)}">${escapeHtml(v.name)}</span>
    <button class="adv-view-del btn-ghost btn-sm" data-id="${escapeHtml(v.id)}" title="删除视图">✕</button>
  </div>`).join("");
  root.querySelectorAll(".adv-view-name").forEach((el) => el.addEventListener("click", () => applyView(el.dataset.id)));
  root.querySelectorAll(".adv-view-del").forEach((b) => b.addEventListener("click", (e) => {
    e.stopPropagation();
    deleteView(b.dataset.id);
  }));
}

function renderAdvLists() {
  const root = $("advLists");
  const lists = window.__lists || [];
  if (!lists.length) {
    root.innerHTML = '<div class="adv-empty">暂无名单</div>';
    return;
  }
  root.innerHTML = lists.map((l) => `<div class="adv-list-item" data-id="${escapeHtml(l.id)}">
    <span class="adv-list-name">${escapeHtml(l.name)}</span>
    <span class="adv-list-kind">${l.kind === "whitelist" ? "白名单" : "黑名单"}</span>
    <span class="adv-list-count">${l.values.length} 项</span>
    <button class="adv-list-del btn-ghost btn-sm" data-id="${escapeHtml(l.id)}" title="删除名单">✕</button>
  </div>`).join("");
  root.querySelectorAll(".adv-list-del").forEach((b) => b.addEventListener("click", () => deleteList(b.dataset.id)));
}

/* ====== 高级筛选：数据加载与持久化 ====== */
async function loadViews() {
  try {
    const d = await (await fetch("/api/views")).json();
    window.__views = d.views || [];
    renderAdvViews();
  } catch (e) { /* 忽略 */ }
}
async function loadLists() {
  try {
    const d = await (await fetch("/api/lists")).json();
    window.__lists = d.lists || [];
    renderAdvLists();
  } catch (e) { /* 忽略 */ }
}
async function saveViewCall(name) {
  const spec = JSON.parse(JSON.stringify(advFilter));
  const res = await fetch("/api/views", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name, spec: spec }),
  });
  const d = await res.json();
  if (d.ok) { window.__views = d.views; renderAdvViews(); }
  return d;
}
async function deleteView(id) {
  const res = await fetch("/api/views", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "delete", id: id }),
  });
  const d = await res.json();
  if (d.ok) { window.__views = d.views; renderAdvViews(); if (activeViewId === id) { activeViewId = null; advFilter.enabled = false; } }
}
async function saveListCall(name, kind, values) {
  const res = await fetch("/api/lists", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name, kind: kind, values: values }),
  });
  const d = await res.json();
  if (d.ok) { window.__lists = d.lists; renderAdvLists(); }
  return d;
}
async function deleteList(id) {
  const res = await fetch("/api/lists", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "delete", id: id }),
  });
  const d = await res.json();
  if (d.ok) { window.__lists = d.lists; renderAdvLists(); }
}

function applyView(id) {
  const v = (window.__views || []).find((x) => x.id === id);
  if (!v) return;
  activeViewId = id;
  // 深拷贝 spec 到 advFilter
  const spec = JSON.parse(JSON.stringify(v.spec));
  advFilter.enabled = true;
  advFilter.logic = spec.logic || "AND";
  advFilter.negate = !!spec.negate;
  advFilter.groups = spec.groups || [];
  advFilter.having = spec.having || null;
  $("advLogic").value = advFilter.logic;
  $("advNegate").checked = advFilter.negate;
  sortFields = spec.sort || [];
  renderAdvGroups(); renderAdvHaving(); renderAdvSort(); renderAdvViews();
  load(true);
  toast("已应用视图：" + v.name);
}

/* ====== 高级筛选：抽屉开关 ====== */
function openAdvDrawer() {
  if (advDrawerOpen) return;
  advDrawerOpen = true;
  loadViews(); loadLists();
  renderAdvGroups(); renderAdvHaving(); renderAdvSort(); renderAdvViews(); renderAdvLists();
  $("advOverlay").classList.remove("hidden");
  const d = $("advDrawer");
  d.classList.remove("hidden");
  d.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => d.classList.add("open"));
  document.body.classList.add("lock-scroll");
}
function closeAdvDrawer() {
  if (!advDrawerOpen) return;
  advDrawerOpen = false;
  const d = $("advDrawer");
  d.classList.remove("open");
  d.setAttribute("aria-hidden", "true");
  $("advOverlay").classList.add("hidden");
  document.body.classList.remove("lock-scroll");
  setTimeout(() => d.classList.add("hidden"), 220);
}

$("advToggle").addEventListener("click", openAdvDrawer);
$("advClose").addEventListener("click", closeAdvDrawer);
$("advOverlay").addEventListener("click", closeAdvDrawer);
$("advLogic").addEventListener("change", (e) => { advFilter.logic = e.target.value; });
$("advNegate").addEventListener("change", (e) => { advFilter.negate = e.target.checked; });
$("advAddGroup").addEventListener("click", () => {
  advFilter.groups.push({ logic: "AND", conditions: [{ field: "progress_pct", op: ">=", value: 0.95 }] });
  renderAdvGroups();
});
$("advAddHaving").addEventListener("click", () => {
  advFilter.having = { groupBy: "author_mid", op: ">", value: 3 };
  renderAdvHaving();
});
$("advAddSort").addEventListener("click", () => {
  sortFields.push({ field: "view_at", dir: "desc" });
  renderAdvSort();
});
$("advSaveView").addEventListener("click", async () => {
  const name = prompt("视图名称：", "我的筛选 " + new Date().toLocaleString());
  if (!name) return;
  const d = await saveViewCall(name);
  if (d.ok) toast("已保存视图：" + name);
  else toast("保存失败");
});
$("advClearView").addEventListener("click", () => {
  activeViewId = null;
  advFilter.enabled = false;
  advFilter.groups = [];
  advFilter.having = null;
  advFilter.negate = false;
  sortFields = [];
  renderAdvGroups(); renderAdvHaving(); renderAdvSort(); renderAdvViews();
  load(true);
});
$("advAddList").addEventListener("click", async () => {
  const name = prompt("名单名称：", "我的UP主名单");
  if (!name) return;
  const kind = confirm("点击「确定」= 白名单（in_list 引用时保留）；「取消」= 黑名单（not_in_list 引用时排除）")
    ? "whitelist" : "blacklist";
  const raw = prompt("输入值（逗号分隔，如 UP主ID 或 类型）：", "");
  if (raw == null) return;
  const values = raw.split(",").map((x) => x.trim()).filter(Boolean);
  const d = await saveListCall(name, kind, values);
  if (d.ok) toast("已保存名单：" + name);
  else toast("保存失败");
});
$("advEnable").addEventListener("click", () => {
  advFilter.enabled = true;
  activeViewId = null;
  closeAdvDrawer();
  load(true);
  toast("高级筛选已应用");
});
$("advDisable").addEventListener("click", () => {
  advFilter.enabled = false;
  activeViewId = null;
  closeAdvDrawer();
  load(true);
  toast("已仅用快捷筛选");
});

/* ====== 批量：新增「归档」动作 ====== */
// 在批量动作下拉中追加归档选项（随视图显示）
function syncBatchKindToViewExt() {
  const sel = $("batchActionSel");
  if (state.view === "all") {
    if (!sel.querySelector('option[value="archive"]')) {
      const opt = document.createElement("option");
      opt.value = "archive"; opt.textContent = "归档选中";
      sel.appendChild(opt);
    }
  }
}
// 拦截批量应用，增加 archive 处理
const _batchApplyOrig = $("batchApply").onclick;
$("batchApply").addEventListener("click", async (e) => {
  if (batchAction !== "archive") return;  // 其他动作由原有逻辑处理
  e.stopImmediatePropagation();
  const cbs = [...listEl.querySelectorAll(".batch-cb:not([disabled]):checked")];
  if (cbs.length === 0) { toast("请先勾选视频"); return; }
  $("batchApply").disabled = true;
  const kids = cbs.map((cb) => cb.dataset.kid);
  try {
    const r = await fetch("/api/batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kids: kids, action: "archive" }),
    });
    const d = await r.json();
    if (d.ok) toast(`已归档 ${d.affected} 条`);
    else toast("归档失败");
  } catch (err) { toast("归档失败：" + err.message); }
  $("batchApply").disabled = false;
  load(true);
}, true);

// 在进入批量模式时补充 archive 选项
const _batchBtnClick = $("batchBtn").onclick;
$("batchBtn").addEventListener("click", () => {
  setTimeout(syncBatchKindToViewExt, 0);
});

/* ====== 数据源健康横幅 + 数据源主开关（#27 / #21）====== */
const SRC_BANNER_KEY = "bhf_src_banner_dismissed";
const SRC_POLL_MS = 300000;   // 5 分钟：凭证查的是 B站 nav，低频即可

function srcBannerDismissed(sig) {
  try { return sessionStorage.getItem(SRC_BANNER_KEY) === sig; } catch (e) { return false; }
}
function srcBannerDismiss(sig) {
  try { sessionStorage.setItem(SRC_BANNER_KEY, sig); } catch (e) {}
}

/* 阶段 4 · `T3-A`：横幅由 6 分支收敛为**两档**（`方案.md` §6.2.2）。
 *
 *   ① 不可用（bad）＝ 原 1 不可达 ＋ 2 凭证失效 ＋ 4 #37 路径诊断 error
 *   ② 降级  （warn）＝ 原 3 local 模式 ＋ 5 #37 库为空 ＋ 6 auto 降级
 *
 * ⚠️ 收敛的**关键不是「砍掉 4 条」，而是「档内要把原因拼出来」** ——
 * `#37` 的路径诊断是有价值提示（配错库路径时不响横幅会白折腾很久），
 * 所以每条原分支的具体文案都保留，只是按档分组。
 * `sig` 由单个分支名改为**档内原因列表**，保证 dismiss（sessionStorage 记忆）
 * 不会因为「先看到 A、后看到 B」而反复重弹。
 *
 * @param s  视图对象（`reachable` 已被 `refreshSourceHealth` 换成业务级 `analyzer.ok`）
 * @param raw 原始 `/api/capabilities` 响应（`sessdata` / `source` 在 `connection` 之外）
 */
function renderSourceBanner(s, raw) {
  const box = $("srcBanner");
  if (!box) return;
  const srcRaw = raw || s || {};
  const sess = (srcRaw.sessdata) || (s && s.sessdata) || {};
  const src = (srcRaw.source) || {};
  const diag = src.diagnosis || {};   // #37：Analyzer 库路径诊断（error=路径配错 / warn=库为空）
  const reachable = !!(s && s.reachable);

  // 逐条判据先算出来，再按档归类（顺序与原 6 分支一致：bad 优先于 warn）
  const reasons = { bad: [], warn: [] };

  if (!reachable) {
    reasons.bad.push("Analyzer 不可达（8899）—— 主源离线"
      + (src.effective === "local" ? "，已降级为本地库 " + (src.local || 0) + " 条" : ""));
  }
  if (reachable && sess.state === "invalid") {
    reasons.bad.push("Analyzer 凭证已失效（-101）—— 抓取会失败，请更新 config/config.yaml 的 SESSDATA");
  }
  if (diag.level === "error") {
    /* #37：路径配错 / 不是 Analyzer 库 / 打不开 —— 必须与「Analyzer 确实没有数据」区分开。
       否则 auto 静默降级成 local 时，你会以为 Analyzer 没数据而去白折腾一遍。 */
    reasons.bad.push(diag.message || "Analyzer 库配置有问题（见设置 ⚙ 的数据源段）");
  }
  if (src.requested === "local") {
    reasons.warn.push("当前为「自身模式（local）」：只用本地 Finder 库 "
      + (src.local || 0) + " 条，历史深度可能不足");
  }
  if (diag.level === "warn") {
    reasons.warn.push(diag.message || "Analyzer 库里没有记录");
  }
  if (src.effective === "local" && src.requested === "auto") {
    reasons.warn.push("auto 模式未能从 Analyzer 读到数据，已自动降级为本地库 "
      + (src.local || 0) + " 条");
  }

  const level = reasons.bad.length ? "bad" : (reasons.warn.length ? "warn" : "ok");
  const list = level === "ok" ? [] : reasons[level];
  // `sig` = 档位 ＋ **档内原因全文**（`T3-A` 的原始口径）—— 只有「完全相同的状态」才沿用
  // 上次的收起；原因一变就再次提示，与 `#srcClose` 的 title「状态变化后会再次提示」一致。
  // （曾误用「档位＋条数」：同档同条数但原因不同的新状态会被静默吞掉。）
  const sig = level + ":" + list.join("|");
  if (level === "ok" || srcBannerDismissed(sig)) { box.classList.add("hidden"); return; }

  box.className = "src-banner " + level;
  // 档内多条原因用「；」拼接，一条横幅承载全部信息
  $("srcText").textContent = list.join("；") + "。";
  const a = $("srcAct");
  a.classList.toggle("hidden", level !== "bad");   // 只有「不可用」才值得引导去设置
  const dot = $("srcDot");
  dot.className = "src-dot " + level;
  box.dataset.sig = sig;
  box.classList.remove("hidden");
}

/** 阶段 4：**轮询切到 `/api/capabilities`**（`方案.md` D2 / 排序约束「切换点 2」）。
 *
 * 为什么必须切（这是本轮最容易做错的一处）：
 *  · `/api/fetcher-health` 的顶层 `ok` 来自 `_forward_fetcher` —— 语义是
 *    「**HTTP 请求成功**」，而 `except HTTPError` 分支里 HTTP 502 也返 `ok=False` 但
 *    `reachable=True`；更关键的是它**没有**「业务级可用」这个字段。
 *  · 真正的判据是 `/api/capabilities` 的 `connection.analyzer.ok`
 *    （＝ `/health` HTTP 成功**且**可达），与后端能力层/策略层/409 门控**同一判据**。
 *    若前端继续用 `s.reachable`，会出现「横幅说连着、按钮全灰」（`现状.md` §8.4 问题 2）。
 *  · **超集兼容**：新端点顶层原样带 `reachable`/`status`/`source`/`sessdata`
 *    （`方案.md` 排序约束 #2）→ 下面两处消费方**不必改**。
 */
function refreshSourceHealth() {
  fetch("/api/capabilities")
    .then((r) => r.json())
    .then((s) => {
      // 业务级判据：从 connection.analyzer.ok 取；取不到则退回旧的传输层（保守：视为不可用）
      const ana = (s && s.connection && s.connection.analyzer) || {};
      const ok = (ana.ok !== undefined) ? !!ana.ok : !!(s && s.reachable);
      const view = Object.assign({}, s, { reachable: ok, analyzerOk: ok });
      applyCapabilities(s.capabilities, ok);             // 表驱动置灰（能力 ＋ 端点归属两维）
      applyFetcherHealth(view);                    // 连接状态点
      renderSourceBanner(view, s);                 // 横幅（T3-A 两档）
    })
    .catch(() => {});
}

$("srcClose").addEventListener("click", () => {
  const box = $("srcBanner");
  srcBannerDismiss(box.dataset.sig || "ok");
  box.classList.add("hidden");
});
$("srcAct").addEventListener("click", () => { openSettings(); });

/* 设置面板里的数据源三选一：读取当前 + 保存 */
function loadSourceCfg() {
  // 阶段 4：改读 `/api/capabilities`（`mode` 已是能力层的输出，不是手切输入）
  fetch("/api/capabilities").then((r) => r.json()).then((d) => {
    // 形态：只读展示（不再是下拉框）—— 由 `analyzer.ok` 决定，见 `方案.md` D1
    const box = $("srcModeBox");
    if (box) {
      const combined = d && d.mode === "combined";
      box.innerHTML = combined
        ? '<b class="ok">组合形态</b>（combined）—— Analyzer 主源 ＋ 本地补齐'
        : '<b class="bad">独立形态</b>（standalone）—— 只用本地 Finder 库';
      box.dataset.mode = d && d.mode ? d.mode : "";
    }
    const st = $("srcStatus");
    const s = (d && d.data) || {};
    if (st) {
      st.textContent = "当前实际生效：" + ((d && d.source && d.source.effective) || "未加载") +
        "（Analyzer " + (s.analyzer || 0) + " 条 / 本地 " + (s.local || 0) + " 条 → 合并 " + (s.merged || 0) + " 条）";
      st.className = "fld-status";
    }
  }).catch(() => {});
}
$("srcSave").addEventListener("click", () => {
  // 阶段 4：**不再提交 mode**（形态由连接决定）—— 只保存 Analyzer 主库路径。
  // 排序约束（`方案.md` §4）：前端先停用三态手切，后端才收紧端点，否则用户会看到「切换失败」。
  const db = $("srcAnalyzerDb").value.trim();
  const st = $("srcStatus");
  if (!db) {
    // label 写的是「留空则用当前值」→ 留空是**合法**输入，不是错误。早先这里把它当错误拦下，
    // 而 `loadSourceCfg()` 并**不回填**当前路径（`/api/capabilities` 也不暴露 analyzer_db）
    // → 每次打开设置输入框都是空的 → 保存按钮实际永远点不动。
    st.textContent = "● 未填写主库路径 —— 沿用当前值，未做任何修改（形态由连接自动决定，无需手切）";
    st.className = "fld-status";
    return;
  }
  st.textContent = "正在保存…"; st.className = "fld-status";
  fetch("/api/data-source", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ analyzer_db: db }),
  }).then((r) => r.json().then((body) => ({ status: r.status, body })))
    .then(({ status, body: s }) => {
      if (status === 409) { st.textContent = "● " + handleApiError(409, s); st.className = "fld-status bad"; return; }
      if (s && s.ok) {
        const x = s.status || {};
        const msg = "已保存主库路径（实际生效 " + (x.effective || "?") +
          "，合并 " + (x.merged || 0) + " 条）";
        // C-H3（2026-10-01 复核修）：后端已如实回报 persisted，前端此前只判顶层 ok →
        // 写盘失败时界面照报"已保存"，F-H2 补的落盘结果成了**死数据**。
        if (s.persisted === false) {
          st.textContent = "● " + msg + "，但配置未写入文件（重启后会退回旧值）";
          st.className = "fld-status bad";
        } else {
          st.textContent = "● " + msg;
          st.className = "fld-status ok";
        }
        refreshSourceHealth();
        load(true);
      } else {
        st.textContent = "● " + ((s && s.error) || "保存失败");
        st.className = "fld-status bad";
      }
    }).catch((e) => { st.textContent = "● 保存失败：" + e.message; st.className = "fld-status bad"; });
});

refreshSourceHealth();
setInterval(() => { if (!document.hidden) refreshSourceHealth(); }, SRC_POLL_MS);
window.addEventListener("focus", () => refreshSourceHealth());

/* ====== ⑧⑨⑩⑪ 导出 / 整库 / 图片批量下载 —— 测试入口（#25 / #23）====== */
function postCall(btn, url, title) {
  // 同 `anCall`：置灰的按钮不发起请求（gray 模式下浏览器不会阻止 click）
  if (btn && btn.classList.contains("cap-disabled")) {
    toast(btn.title || "当前能力不可用");
    return;
  }
  if (btn) btn.disabled = true;
  toast("请求 Analyzer：" + title + " …");
  fetch(url, { method: "POST" })
    .then((r) => r.json().then((body) => ({ status: r.status, body })))
    .then(({ status, body: s }) => {
      if (status === 409) { handleApiError(409, s); showDiag(title + " — 不可用", s); return; }
      showDiag(title + " — 响应", s); checkFetcher();
    })
    .catch((e) => showDiag(title + " — 错误", { error: e.message }))
    .finally(() => { if (btn) btn.disabled = false; });
}

/* ⑧ 导出 Excel：转发 Analyzer 生成 xlsx，再经 /api/export/excel/{file} 下载 */
$("anExportBtn").addEventListener("click", () => {
  const btn = $("anExportBtn");
  const y = prompt("导出年份（YYYY，留空 = 不传 year 由 Analyzer 决定）",
                   String(new Date().getFullYear()));
  if (y === null) return;
  const q = y.trim() ? ("?year=" + encodeURIComponent(y.trim())) : "";
  btn.disabled = true;
  toast("正在让 Analyzer 生成 Excel…");
  fetch("/api/export/excel" + q, { method: "POST" })
    .then((r) => r.json())
    .then((s) => {
      const fn = s && s.data && s.data.filename;
      if (s && s.ok && fn) {
        showDiag("⑧ 导出 Excel — 已生成，开始下载\n" + fn, s);
        window.location.href = "/api/export/excel/" + encodeURIComponent(fn);
      } else {
        showDiag("⑧ 导出 Excel — 失败", s);
      }
    })
    .catch((e) => showDiag("⑧ 导出 Excel — 错误", { error: e.message }))
    .finally(() => { btn.disabled = false; });
});

/* ⑨ 下载整库 .db：直接走 windows 下载流 */
$("anDbBtn").addEventListener("click", () => {
  if (!confirm("将下载 Analyzer 整库 .db。\n注意：这是 Analyzer 的原始数据库快照，下载后请自行妥善保管。\n\n确认下载？")) return;
  window.location.href = "/api/export/db";
});

/* ⬇ 本地库导出（阶段 4 新增）：**不转发 Analyzer**，直读本地 Finder 库。
 * 这是「独立形态下唯一还能用的导出出口」→ 故它不进 `CAP_DOM`、不参与门控、恒可用。
 * 错误处理走 `handleApiError()`（409 统一消费，`T10`）。 */
$("anLocalExportBtn").addEventListener("click", () => {
  const btn = $("anLocalExportBtn");
  if (btn.classList.contains("cap-disabled")) {
    toast(CAP_HINT["export"] || "当前不可用");
    return;
  }
  if (!confirm("将下载**本地 Finder 库**的快照（bilibili_history.db）。\n"
             + "注意：这是本地浏览库的副本，与 Analyzer 整库不是同一份数据。\n\n确认下载？")) return;
  btn.disabled = true;
  toast("正在导出本地库…");
  // 用 fetch 先探一次状态（而不是直接改 location）—— 404/409 时能给出可读提示，
  // 成功才触发浏览器下载流。
  fetch("/api/export/local/db")
    .then(async (r) => {
      if (!r.ok) {
        let msg = "HTTP " + r.status;
        try { const j = await r.json(); msg = j.error || msg; } catch (e) { /* 非 JSON 体，保留状态码 */ }
        return Promise.reject(new Error(msg));
      }
      return r.blob();
    })
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "bilibili_history_local.db";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast("已导出本地库（" + (blob.size / 1024).toFixed(0) + " KB）");
    })
    .catch((e) => toast("本地库导出失败：" + e.message))
    .finally(() => { btn.disabled = false; });
});

/* ⑩ 图片状态（只读） */
$("anImgBtn").addEventListener("click", () =>
  anCall($("anImgBtn"), "/api/images/status", "⑩ 图片状态 (/images/status)"));

/* ⑪ 下载图片（写盘操作，默认不用凭证 + 限定年份的安全冒烟组合） */
$("anImgStartBtn").addEventListener("click", () => {
  const y = prompt("年份（YYYY = 只下该年；留空 = 全部年份，量大）",
                   String(new Date().getFullYear()));
  if (y === null) return;
  const useSess = confirm(
    "下载时是否使用 SESSDATA？\n\n" +
    "【取消】= 不使用（推荐：封面/头像属公开内容，无需凭证）\n" +
    "【确定】= 使用（需 Analyzer 凭证有效）");
  const q = "?use_sessdata=" + (useSess ? "true" : "false") +
            (y.trim() ? "&year=" + encodeURIComponent(y.trim()) : "");
  if (!confirm("⚠️ 这是写盘操作：会往 Analyzer 的输出目录批量写入封面/头像文件。\n" +
               "参数：year=" + (y.trim() || "全部") + "，use_sessdata=" + useSess + "\n\n确认开始？")) return;
  postCall($("anImgStartBtn"), "/api/images/start" + q, "⑪ 开始下载图片");
});

$("anImgStopBtn").addEventListener("click", () =>
  postCall($("anImgStopBtn"), "/api/images/stop", "⑪ 停止下载图片"));

/* ====== #24 remark 备注：卡片上点击即编辑（写回 Analyzer 主库，与 Frontend / 官网互通）====== */
document.addEventListener("click", (e) => {
  const el = e.target && e.target.closest ? e.target.closest(".remark-text") : null;
  if (!el) return;
  const row = el.closest(".remark-row");
  if (!row) return;
  const bvid = row.dataset.bvid || "";
  const viewAt = row.dataset.viewAt || "";
  if (!bvid || !viewAt) { toast("该记录无 bvid（直播/专栏不支持备注）"); return; }
  const cur = el.classList.contains("empty") ? "" : el.textContent;
  const next = prompt("备注（写回 Analyzer 主库；留空即清除）", cur);
  if (next === null) return;
  el.textContent = "保存中…";
  fetch("/api/remark", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid, view_at: Number(viewAt), remark: next }),
  }).then((r) => r.json()).then((s) => {
    if (s && s.ok) {
      el.textContent = next || "＋ 添加备注";
      el.className = remarkClass(next);   // #36 同步 empty / sys（编辑后可能已不是系统标记）
      el.title = remarkTitle(next);
      toast(next ? "备注已保存" : "备注已清除");
    } else {
      el.textContent = cur || "＋ 添加备注";
      toast("保存失败：" + ((s && s.error) || "未知错误"));
    }
  }).catch((err) => {
    el.textContent = cur || "＋ 添加备注";
    toast("保存失败：" + err.message);
  });
});
