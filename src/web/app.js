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

/* ====== 构建 API 参数 ====== */

function buildParams(reset) {
  if (reset) offset = 0;
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  if (state.biz) p.set("business", state.biz);
  if (state.view && state.view !== "all") p.set("view", state.view);
  if (state.archived) p.set("archived_only", "1");

  // 时间筛选：快捷预设优先，否则用面板日期
  const tr = timeRangeToTs(state.timeRange);
  if (tr) {
    p.set("date_from", String(tr.from));
    p.set("date_to", String(tr.to));
  } else {
    if (state.dateFrom) p.set("date_from", state.dateFrom);
    if (state.dateTo) p.set("date_to", state.dateTo);
  }

  // 时长筛选
  const dr = durToSec(state.dur);
  if (dr) {
    p.set("duration_min", String(dr.min));
    p.set("duration_max", String(dr.max));
  }

  // 设备筛选
  if (state.dt) p.set("dt", state.dt);

  p.set("limit", String(PAGE));
  p.set("offset", String(offset));
  return p;
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
    const res = await fetch("/api/history?" + buildParams(!!reset).toString());
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
    const num = (typeof v === "number") ? v : (v != null ? v : "");
    return `<input type="number" class="val-num" data-ri="${ri}" data-g="${gi}" data-c="${ci}" data-k="value" value="${escapeHtml(String(num))}" step="any" />`;
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
    } else if (range === "older") {
      const w = new Date(); w.setDate(w.getDate() - 7);
      state.dateFrom = String(Math.floor(new Date(w.getFullYear(), w.getMonth(), w.getDate()).getTime() / 1000));
      state.timeRange = "";
    } else if (range === "month") {
      const m = new Date(); m.setDate(m.getDate() - 30);
      state.dateFrom = String(Math.floor(new Date(m.getFullYear(), m.getMonth(), m.getDate()).getTime() / 1000));
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
setInterval(pollSync, 3000);
pollSync();

/* ====== 初始加载 ====== */
loadRules();
load(true);
pollRuleStatus();
