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

// 批量勾选模式状态
let batchMode = false;        // 是否进入批量模式
let batchAction = "skip";     // "skip"=批量跳过 / "restore"=批量恢复

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

  // 跳过状态（三态：manual / auto / 空）；徽标放在封面右上角，可点击取消
  const skipState = it.skip_state || "";
  const skipBadge = skipState === "manual"
    ? `<span class="badge-skip manual cancelable" data-kid="${escapeHtml(it.kid)}" title="手动跳过：点击取消（同步不会回退其进度）">已跳过·手动</span>`
    : skipState === "auto"
    ? `<span class="badge-skip auto cancelable" data-kid="${escapeHtml(it.kid)}" title="按规则自动跳过：点击取消">已跳过·自动</span>`
    : "";
  // 右上角（未开播等）：有跳过徽标时让位
  let trBadge = "";
  if (!skipState && it.business === "live" && it.live_status != 1) trBadge = '<span class="badge-tr">未开播</span>';

  // 左下角：仅本地存档徽标（§12.4，存档状态仅初始全量校准）
  const archBadge = (it.archived_only == 1)
    ? '<span class="badge-arch" title="B站端已无此记录，仅本地留存；存档状态仅初始全量校准">仅本地存档</span>'
    : "";

  // 操作按钮：
  // - 需要观看生效：用手动跳过按钮（替换删除按钮，更醒目）；已跳过的显示「取消跳过」可反悔
  // - 完整历史：保留网络数据原貌，仅显示删除（隐藏）按钮，跳过状态由封面右上角徽标表达（可点击取消）
  const needsActive = state.needs;
  let actionBtn;
  if (needsActive) {
    if (skipState) {
      actionBtn = `<button class="skip-toggle on" data-kid="${escapeHtml(it.kid)}" data-act="cancel" title="已标记为不需要观看，点击恢复">取消跳过</button>`;
    } else {
      actionBtn = `<button class="skip-toggle" data-kid="${escapeHtml(it.kid)}" data-act="skip" title="标记为不需要观看（同步不会回退其进度）">不需要看?</button>`;
    }
  } else {
    actionBtn = `<button class="del-btn" title="隐藏此条" data-kid="${escapeHtml(it.kid)}">🗑</button>`;
  }

  // 批量勾选框（仅批量模式显示；按模式限定可勾选集合）
  // - delete（完整历史）：所有卡片可勾选（对应 🗑 删除/隐藏）
  // - skip（需要观看）：仅未跳过可勾选；restore：仅已跳过可勾选
  let cb = "";
  if (batchMode) {
    const eligible = batchAction === "delete"
      ? true
      : (batchAction === "skip" ? !skipState : !!skipState);
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

/* ====== 本项目特殊筛选（工具栏中间：需要观看 / 含存档）===== */
$("spNeeds").addEventListener("click", () => {
  state.needs = !state.needs;
  $("spNeeds").classList.toggle("active", state.needs);
  syncBatchKindToView();   // 批量模式下联动切换 删除/跳过 动作
  load(true);
});
$("spArch").addEventListener("click", () => {
  state.archived = !state.archived;
  $("spArch").classList.toggle("active", state.archived);
  load(true);
});

/* ====== 更多筛选面板开关（需要观看旁的下拉） ====== */
const filterPanel = $("filterPanel");
const needFilterToggle = $("needFilterToggle");
needFilterToggle.addEventListener("click", () => {
  filterPanel.classList.toggle("hidden");
  needFilterToggle.classList.toggle("open");
});

/* ====== 自动跳过规则：加载 / 持久化（data/filters.ini） ====== */
async function loadFilters() {
  try {
    const d = await (await fetch("/api/filters")).json();
    const biz = d.business || [];
    document.querySelectorAll(".biz-chk").forEach((c) => {
      c.checked = biz.includes(c.value);
    });
    $("minDur").value = d.min_duration_min || 0;
    $("authors").value = (d.authors || []).join(", ");
  } catch (e) { /* 忽略：使用默认勾选 */ }
}

function collectFilters() {
  const business = [...document.querySelectorAll(".biz-chk:checked")].map((c) => c.value);
  const min_duration_min = parseInt($("minDur").value, 10) || 0;
  const authors = $("authors").value.split(",").map((s) => s.trim()).filter(Boolean);
  return { business, min_duration_min, authors };
}

function saveFilters() {
  fetch("/api/filters", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collectFilters()),
  }).then((r) => r.json()).then((d) => {
    if (!d.ok) toast("保存筛选条件失败");
  }).catch(() => {});
}

document.querySelectorAll(".biz-chk").forEach((c) => {
  c.addEventListener("change", saveFilters);
});
$("minDur").addEventListener("change", saveFilters);
let authorsTimer = null;
$("authors").addEventListener("input", () => {
  clearTimeout(authorsTimer);
  authorsTimer = setTimeout(saveFilters, 500);
});

/* ====== 应用为自动跳过（全量重扫，带进度屏蔽） ====== */
$("applyAutoSkip").addEventListener("click", () => {
  if (autoskipRunning) return;
  fetch("/api/apply-autoskip", { method: "POST" })
    .then((r) => r.json())
    .then((d) => {
      if (d.blocked) { toast(d.reason || "已有任务进行中"); return; }
      if (d.started) startAutoSkipPoll();
    })
    .catch(() => toast("触发自动跳过失败"));
});

let autoskipRunning = false;
function startAutoSkipPoll() {
  const prog = $("autoSkipProgress");
  const bar = prog.querySelector(".sync-bar > i");
  const step = $("autoSkipStep");
  prog.classList.remove("hidden");
  $("applyAutoSkip").disabled = true;
  autoskipRunning = true;
  const tick = () => {
    fetch("/api/auto-skip-status").then((r) => r.json()).then((s) => {
      const p = s.progress || {};
      const denom = p.total || 0;
      let pct = 0;
      if (p.completed) pct = 100;
      else if (denom > 0 && p.done) pct = Math.min(99, Math.round((p.done / denom) * 100));
      bar.style.width = pct + "%";
      step.textContent = p.completed
        ? `自动跳过应用完成：${p.auto_set || 0} 条被标记`
        : `正在应用自动跳过…（${p.done || 0}/${denom}）`;
      if (s.running) {
        setTimeout(tick, 400);
      } else {
        prog.classList.add("hidden");
        $("applyAutoSkip").disabled = false;
        autoskipRunning = false;
        if (s.last && s.last.ok) {
          toast(`已应用自动跳过：${s.last.auto_set} 条标记为跳过`
            + (s.last.manual_cleared ? `（覆盖手动 ${s.last.manual_cleared} 条）` : ""));
          load(true);
        } else if (s.last) {
          toast("自动跳过失败：" + (s.last.err || ""));
        }
      }
    }).catch(() => {
      prog.classList.add("hidden");
      $("applyAutoSkip").disabled = false;
      autoskipRunning = false;
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

/* ====== 删除 / 手动跳过（就地切换，可反悔）/ 徽标取消 ====== */
listEl.addEventListener("click", (e) => {
  // 手动跳过按钮（需要观看视图下替换删除按钮）
  const skipToggle = e.target.closest(".skip-toggle");
  if (skipToggle) {
    e.preventDefault();
    const kid = skipToggle.dataset.kid;
    const act = skipToggle.dataset.act; // "skip" 或 "cancel"
    const li = skipToggle.closest(".item");
    const val = act === "cancel" ? 0 : 1;
    fetch(`/api/skip?kid=${encodeURIComponent(kid)}&value=${val}`, { method: "POST" })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) { toast("操作失败：" + (d.error || "")); return; }
        // 就地更新按钮与卡片状态，不移除（保留可反悔）
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
      })
      .catch(() => toast("操作失败"));
    return;
  }
  // 封面右上角跳过徽标：点击取消（完整历史 / 任意视图均可反悔）
  const badge = e.target.closest(".badge-skip.cancelable");
  if (badge) {
    e.preventDefault();
    const kid = badge.dataset.kid;
    const li = badge.closest(".item");
    fetch(`/api/skip?kid=${encodeURIComponent(kid)}&value=0`, { method: "POST" })
      .then((r) => r.json())
      .then((d) => {
        if (!d.ok) { toast("取消失败：" + (d.error || "")); return; }
        if (li) li.dataset.skip = "";
        badge.remove();
        toast("已取消跳过");
      })
      .catch(() => toast("取消失败"));
    return;
  }
  // 删除（隐藏）按钮
  const btn = e.target.closest(".del-btn");
  if (!btn) return;
  e.preventDefault();
  const li = btn.closest(".item");
  if (li) li.remove();
  toast("已隐藏（刷新后恢复）");
});

/* ====== 批量勾选模式 ====== */
// 视图联动：进入批量模式时，按当前视图决定动作类型
// - 完整历史（needs 关）：批量 = 删除（隐藏），对应 🗑 图标
// - 需要观看（needs 开）：批量 = 跳过/恢复，对应「不需要看?」按钮
function syncBatchKindToView() {
  if (!batchMode) return;
  const sel = $("batchActionSel");
  const tag = $("batchViewTag");
  if (state.needs) {
    // 需要观看：批量 = 跳过 / 恢复（下拉仅给这两个选项，与「完整历史」明显不同）
    sel.innerHTML =
      '<option value="skip">跳过选中</option>' +
      '<option value="restore">恢复选中</option>';
    if (batchAction !== "skip" && batchAction !== "restore") batchAction = "skip";
    tag.textContent = "视图：需要观看";
    tag.className = "batch-view-tag tag-needs";
  } else {
    // 完整历史：批量 = 删除（隐藏），下拉仅一个选项，明确与「需要观看」区分
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
    toast(state.needs ? "批量模式：勾选视频后「跳过」或「恢复」" : "批量模式：勾选视频后「删除选中」（隐藏）");
  }
  load(true);
});

function syncBatchButtons() {
  updateBatchEligibility();
}

// 下拉切换批量动作（选项已按当前视图限定，故切换即合法）
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
    let ok;
    if (batchAction === "delete") ok = true;          // 完整历史：所有卡片可删除
    else if (batchAction === "skip") ok = !st;         // 跳过模式：仅未跳过
    else ok = !!st;                                    // 恢复模式：仅已跳过
    cb.disabled = !ok;
    li.classList.toggle("batch-disabled", !ok);
    if (ok) eligible++;
    if (ok && cb.checked) checked++;
  });
  const hint = batchAction === "delete"
    ? `删除模式：勾选视频后「删除选中」即可隐藏（共 ${eligible} 条可操作）`
    : batchAction === "skip"
      ? `跳过模式：仅可勾选「未跳过」的视频（共 ${eligible} 条可操作）`
      : `恢复模式：仅可勾选「已跳过」的视频（共 ${eligible} 条可操作）`;
  $("batchHint").textContent = hint;
  $("batchCount").textContent = checked;
}

// 勾选变化实时更新计数
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
    // 完整历史：批量删除（隐藏），与 🗑 图标行为一致（仅本次会话，刷新恢复）
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
  // 需要观看：批量跳过 / 恢复
  const val = batchAction === "skip" ? 1 : 0;
  let okN = 0;
  for (const cb of cbs) {
    const kid = cb.dataset.kid;
    try {
      const r = await fetch(`/api/skip?kid=${encodeURIComponent(kid)}&value=${val}`, { method: "POST" });
      const d = await r.json();
      if (d.ok) okN++;
    } catch (e) { /* 忽略单条失败，继续 */ }
  }
  $("batchApply").disabled = false;
  toast(`已${batchAction === "skip" ? "跳过" : "恢复"} ${okN} 条`);
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
    const modeText =
      pr.mode === "full" ? "正在全量建基线" :
      pr.mode === "incremental" ? "正在增量同步" :
      pr.mode === "retry" ? "同步中断，正在重试" :
      "正在同步历史记录";
    stepEl.textContent =
      `${modeText}…（已拉取 ${pr.fetched || 0} 条 / 第 ${pr.page || 0} 页，进度 ${pct}%）`;
  } else {
    if (s.last && s.last.ok) {
      // 完成：隐藏进度条，显示绿色「已完成」
      progressEl.classList.add("hidden");
      doneEl.className = "sync-done";
      doneEl.classList.remove("hidden");
      const m = s.last.mode === "incremental" ? "，增量"
        : (s.last.mode === "full" ? "，全量" : "");
      doneEl.textContent = (s.last.fetched != null)
        ? `✓ 同步完成（${s.last.fetched} 条${m}）`
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
$("fullBtn").addEventListener("click", () => {
  fetch("/api/sync?full=1", { method: "POST" })
    .then(() => toast("已触发全量重建：将重新校准存档标记并刷新封面"))
    .then(() => pollSync());
});
setInterval(pollSync, 3000);
pollSync();

/* ====== 初始加载 ====== */
loadFilters();   // 拉取持久化在 filters.ini 的自动跳过规则，回填面板
load(true);
