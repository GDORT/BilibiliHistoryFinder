"use strict";

const PAGE = 60;
let offset = 0;
let total = 0;
let loading = false;

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
        <span class="remark-text${it.remark ? "" : " empty"}" title="点击编辑备注（写回 Analyzer 主库，与 Frontend/官网互通）">${it.remark ? escapeHtml(it.remark) : "＋ 添加备注"}</span>
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
  const ok = !!(s && s.ok && s.reachable);
  dot.classList.toggle("on", ok);
  dot.classList.toggle("off", !ok);
  dot.title = ok ? "Analyzer 已连接" : ("Analyzer 未连接：" + ((s && s.error) || "8899 不可达"));
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
  if (btn) btn.disabled = true;
  toast("请求 Analyzer：" + title + " …");
  fetch(url)
    .then((r) => r.json())
    .then((s) => {
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

/* 依据 /api/fetcher-health 的 reachable + sessdata + source 计算横幅状态（3 态） */
function renderSourceBanner(s) {
  const box = $("srcBanner");
  if (!box) return;
  const reachable = !!(s && s.ok && s.reachable);
  const sess = (s && s.sessdata) || {};
  const src = (s && s.source) || {};
  let level = "ok", sig = "ok", text = "", act = "";

  if (!reachable) {
    level = "bad"; sig = "unreachable";
    text = "Analyzer 不可达（8899）—— 主源离线。" +
           (src.effective === "local" ? "已降级为本地库 " + (src.local || 0) + " 条。" : "");
    act = "打开设置";
  } else if (sess.state === "invalid") {
    level = "bad"; sig = "sessdata-invalid";
    text = "Analyzer 凭证已失效（-101）—— 抓取/更新会失败，请更新 config/config.yaml 的 SESSDATA。";
    act = "打开设置";
  } else if (src.requested === "local") {
    level = "warn"; sig = "mode-local";
    text = "当前为「自身模式（local）」：只用本地 Finder 库 " + (src.local || 0) + " 条，历史深度可能不足。";
    act = "打开设置";
  } else if (src.effective === "local" && src.requested === "auto") {
    level = "warn"; sig = "auto-degraded";
    text = "auto 模式未能从 Analyzer 读到数据，已自动降级为本地库 " + (src.local || 0) + " 条。";
    act = "打开设置";
  }

  if (level === "ok" || srcBannerDismissed(sig)) { box.classList.add("hidden"); return; }
  box.className = "src-banner " + level;
  $("srcText").textContent = text;
  const a = $("srcAct");
  a.classList.toggle("hidden", !act);
  const dot = $("srcDot");
  dot.className = "src-dot " + level;
  box.dataset.sig = sig;
  box.classList.remove("hidden");
}

function refreshSourceHealth() {
  fetch("/api/fetcher-health")
    .then((r) => r.json())
    .then((s) => { applyFetcherHealth(s); renderSourceBanner(s); })
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
  fetch("/api/data-source").then((r) => r.json()).then((d) => {
    const sel = $("srcMode");
    if (sel && d && d.mode) sel.value = d.mode;
    const st = $("srcStatus");
    if (st && d && d.status) {
      const s = d.status;
      st.textContent = "当前实际生效：" + (s.effective || "未加载") +
        "（Analyzer " + (s.analyzer || 0) + " 条 / 本地 " + (s.local || 0) + " 条 → 合并 " + (s.merged || 0) + " 条）";
      st.className = "fld-status";
    }
  }).catch(() => {});
}
$("srcSave").addEventListener("click", () => {
  const mode = $("srcMode").value;
  const db = $("srcAnalyzerDb").value.trim();
  const st = $("srcStatus");
  st.textContent = "正在切换…"; st.className = "fld-status";
  fetch("/api/data-source", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(db ? { mode: mode, analyzer_db: db } : { mode: mode }),
  }).then((r) => r.json()).then((s) => {
    if (s && s.ok) {
      const x = s.status || {};
      st.textContent = "● 已切换为 " + s.mode + "（实际生效 " + (x.effective || "?") +
        "，合并 " + (x.merged || 0) + " 条）";
      st.className = "fld-status ok";
      refreshSourceHealth();
      load(true);
    } else {
      st.textContent = "● " + ((s && s.error) || "切换失败");
      st.className = "fld-status bad";
    }
  }).catch((e) => { st.textContent = "● 切换失败：" + e.message; st.className = "fld-status bad"; });
});

refreshSourceHealth();
setInterval(() => { if (!document.hidden) refreshSourceHealth(); }, SRC_POLL_MS);
window.addEventListener("focus", () => refreshSourceHealth());

/* ====== ⑧⑨⑩⑪ 导出 / 整库 / 图片批量下载 —— 测试入口（#25 / #23）====== */
function postCall(btn, url, title) {
  if (btn) btn.disabled = true;
  toast("请求 Analyzer：" + title + " …");
  fetch(url, { method: "POST" })
    .then((r) => r.json())
    .then((s) => { showDiag(title + " — 响应", s); checkFetcher(); })
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
      el.classList.toggle("empty", !next);
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
