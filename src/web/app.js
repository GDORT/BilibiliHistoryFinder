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
  needs: false,
  archived: false,
  dateFrom: "",
  dateTo: "",
};

const $ = (id) => document.getElementById(id);
const listEl = $("list");
const emptyEl = $("empty");
const loadMoreEl = $("loadMore");
const statsEl = $("stats");

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
  if (state.needs) p.set("needs_watching", "1");
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

  // 右上角（未开播等）
  let trBadge = "";
  if (it.business === "live" && it.live_status != 1) trBadge = '<span class="badge-tr">未开播</span>';

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

  return `
  <li class="item">
    <a class="thumb" href="${escapeHtml(webUrl)}" target="_blank" rel="noopener">
      <img class="cover" src="${coverSrc}" alt="" loading="lazy" onerror="this.style.visibility='hidden'" />
      ${tlBadge}
      ${trBadge}
      ${durOverlay}
      ${pbar}
    </a>
    <div class="meta">
      <div class="title-row">
        <a class="title" href="${escapeHtml(webUrl)}" target="_blank" rel="noopener">${escapeHtml(it.title)}</a>
        <button class="del-btn" title="隐藏此条" data-kid="${escapeHtml(it.kid)}">🗑</button>
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

/* ====== 本项目特殊筛选（工具栏中间：需要观看 / 含存档）===== */
$("spNeeds").addEventListener("click", () => {
  state.needs = !state.needs;
  $("spNeeds").classList.toggle("active", state.needs);
  load(true);
});
$("spArch").addEventListener("click", () => {
  state.archived = !state.archived;
  $("spArch").classList.toggle("active", state.archived);
  load(true);
});

/* ====== 更多筛选面板开关 ====== */
const filterPanel = $("filterPanel");
const filterToggle = $("filterToggle");
filterToggle.addEventListener("click", () => {
  filterPanel.classList.toggle("hidden");
  filterToggle.classList.toggle("open");
});

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

/* ====== 删除按钮（仅本次浏览隐藏）===== */
listEl.addEventListener("click", (e) => {
  const btn = e.target.closest(".del-btn");
  if (!btn) return;
  e.preventDefault();
  const li = btn.closest(".item");
  if (li) li.remove();
  toast("已隐藏（刷新后恢复）");
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

  // 进度百分比：分母取 progress.total_estimate，缺失时回退 meta.total（不必无限动画）
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
    // 同步中：显示进度条（真实百分比）+ 步骤文字
    progressEl.classList.remove("hidden");
    doneEl.classList.add("hidden");
    barEl.style.width = pct + "%";
    stepEl.textContent =
      `正在同步历史记录…（已拉取 ${pr.fetched || 0} 条 / 第 ${pr.page || 0} 页，进度 ${pct}%）`;
  } else {
    if (s.last && s.last.ok) {
      // 完成：隐藏进度条，显示绿色「已完成」
      progressEl.classList.add("hidden");
      doneEl.className = "sync-done";
      doneEl.classList.remove("hidden");
      doneEl.textContent = (s.last.fetched != null)
        ? `✓ 同步完成（${s.last.fetched} 条）`
        : "✓ 同步完成";
    } else if (s.last && !s.last.ok) {
      // 未完成：隐藏进度条，显示红色提示（不更新数据版本）
      progressEl.classList.add("hidden");
      doneEl.className = "sync-done sync-fail";
      doneEl.classList.remove("hidden");
      doneEl.textContent = "⚠ 同步未完成：" + (s.last.err || "请重试");
    } else {
      progressEl.classList.add("hidden");
      doneEl.classList.add("hidden");
    }
  }

  // 常驻：数据版本 + 更新时间（+ 总条数）
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
setInterval(pollSync, 3000);
pollSync();

/* ====== 初始加载 ====== */
load(true);
