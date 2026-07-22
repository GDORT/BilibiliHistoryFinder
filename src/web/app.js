"use strict";

const PAGE = 60;
let offset = 0;
let total = 0;
let loading = false;

const $ = (id) => document.getElementById(id);
const listEl = $("list");
const emptyEl = $("empty");
const loadMoreEl = $("loadMore");
const statsEl = $("stats");

function params() {
  const p = new URLSearchParams();
  const q = $("q").value.trim();
  if (q) p.set("q", q);
  const biz = $("business").value;
  if (biz) p.set("business", biz);
  if ($("needs").checked) p.set("needs_watching", "1");
  if ($("archived").checked) p.set("archived_only", "1");
  const df = $("dateFrom").value;
  if (df) p.set("date_from", String(Math.floor(new Date(df + "T00:00:00").getTime() / 1000)));
  const dt = $("dateTo").value;
  if (dt) p.set("date_to", String(Math.floor(new Date(dt + "T23:59:59").getTime() / 1000)));
  p.set("limit", String(PAGE));
  p.set("offset", String(offset));
  return p;
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function pctOf(it) {
  if (it.progress == null || it.duration == null || it.duration <= 0) return null;
  if (it.progress === -1) return 100;
  return Math.round((it.progress / it.duration) * 100);
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function cardHtml(it) {
  const pct = pctOf(it);
  const need = pct != null && pct < 95;
  const coverSrc = "/cover/" + encodeURIComponent(it.kid);
  const webUrl = it.bvid ? `https://www.bilibili.com/video/${it.bvid}` : (it.uri || "#");
  const appUrl = it.bvid ? `bilibili://video/${it.bvid}` : (it.uri || "#");
  const archBadge = it.archived_only ? `<span class="badge arch">仅本地存档</span>` : "";
  const bizLabel = { archive: "视频", pgc: "番剧", live: "直播", article: "专栏" }[it.business] || it.business || "";
  let progressHtml = "";
  if (pct != null) {
    const cls = pct >= 95 ? "progress full" : "progress";
    progressHtml = `<div class="${cls}"><i style="width:${pct}%"></i></div><div class="pct">已看 ${pct}%</div>`;
  }
  return `
  <li class="item ${need ? "need" : ""}">
    <img class="cover" src="${coverSrc}" alt="" loading="lazy"
         onerror="this.style.visibility='hidden'" />
    <div class="meta">
      <div class="title"><a href="${escapeHtml(webUrl)}" target="_blank" rel="noopener">${escapeHtml(it.title)}</a>${archBadge}</div>
      <div class="sub">
        <span class="up">${escapeHtml(it.author_name || "未知UP")}</span>
        ${it.bvid ? `<span>BV: ${escapeHtml(it.bvid)}</span>` : ""}
        ${bizLabel ? `<span>${escapeHtml(bizLabel)}</span>` : ""}
        <span>${fmtTime(it.view_at)}</span>
      </div>
      ${progressHtml}
      <div class="links">
        <a href="${escapeHtml(webUrl)}" target="_blank" rel="noopener">网页打开</a>
        <a href="${escapeHtml(appUrl)}">App 打开</a>
      </div>
    </div>
  </li>`;
}

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
  statsEl.textContent = `共 ${total} 条，已显示 ${offset}`;
  loadMoreEl.classList.toggle("hidden", offset >= total);
}

async function load(reset) {
  if (loading) return;
  if (reset) offset = 0;
  loading = true;
  try {
    const res = await fetch("/api/history?" + params().toString());
    const data = await res.json();
    render(data);
  } catch (e) {
    toast("加载失败：" + e.message);
  } finally {
    loading = false;
  }
}

let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2500);
}

// 搜索防抖
let searchTimer = null;
$("q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => load(true), 350);
});
["business", "needs", "archived", "dateFrom", "dateTo"].forEach((id) => {
  $(id).addEventListener("change", () => load(true));
});
$("moreBtn").addEventListener("click", () => load(false));

// 同步
function pollSync() {
  fetch("/api/sync").then((r) => r.json()).then((s) => {
    const btn = $("syncBtn");
    if (s.running) {
      btn.disabled = true;
      btn.textContent = "同步中…";
    } else {
      btn.disabled = false;
      btn.textContent = "立即同步";
      if (s.last && s.last.at && s._shown !== s.last.at) {
        s._shown = s.last.at;
        toast(s.last.ok ? "同步完成" : "同步失败：" + (s.last.err || ""));
        load(true);
      }
    }
  });
}
$("syncBtn").addEventListener("click", () => {
  fetch("/api/sync", { method: "POST" }).then(() => {
    toast("已开始同步（全量，后台进行）");
    pollSync();
  });
});
setInterval(pollSync, 3000);

// 初始加载
load(true);
