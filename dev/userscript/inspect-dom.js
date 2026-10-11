// ============================================================================
// BHF DOM 探针（C4 回填用）—— 在「已装主脚本且已抓到数据」的 B站历史页控制台粘贴运行一次。
// 输出一段 JSON（也会弹窗方便复制），把整段贴回给开发者即可填写 runtime.js 的 CFG。
//
// 前置：① 已用 Tampermonkey 装好 dist/bili_history.user.js；
//       ② 打开 https://www.bilibili.com/history 并向下滚动，直到顶栏显示「已抓取 N 条」（N>0）。
//       ③ F12 打开控制台 → 粘贴本脚本 → 回车。
// ============================================================================
(async function () {
  if (!location.href.includes("bilibili.com/history")) {
    console.log("[BHF-INSPECT] 请在 B站历史页（https://www.bilibili.com/history）运行本脚本");
    return;
  }
  function selOf(el) {
    if (!el) return null;
    var tag = (el.tagName || "").toLowerCase();
    if (el.id) return tag + "#" + el.id;
    var cls = (typeof el.className === "string" ? el.className : "").trim().split(/\s+/).filter(Boolean);
    if (cls.length) return tag + "." + cls.slice(0, 2).join(".");
    return tag;
  }
  function attrValues(el) {
    var o = {};
    for (var i = 0; i < el.attributes.length; i++) o[el.attributes[i].name] = el.attributes[i].value;
    return o;
  }
  var linkSel = 'a[href*="/video/"], a[href*="bilibili.com/bangumi"], a[href*="/opus/"], a[href*="/read/"]';

  // 候选容器：含较多内容链接的祖先元素
  var els = Array.prototype.slice.call(document.querySelectorAll("*"));
  var scored = [];
  els.forEach(function (el) {
    if (el.children && el.children.length > 1) {
      var n = el.querySelectorAll(linkSel).length;
      if (n >= 3) scored.push({ el: el, n: n });
    }
  });
  scored.sort(function (a, b) { return b.n - a.n; });

  function cardOf(container) {
    var a = container.querySelector(linkSel);
    if (!a) return null;
    var p = a.parentElement;
    while (p && p !== container) {
      if (p.children && p.children.length >= 2 && p.querySelector(linkSel)) return p;
      p = p.parentElement;
    }
    return a;
  }

  var out = { url: location.href, containers: [], cards: [], stored: [], note: "" };
  scored.slice(0, 4).forEach(function (s) {
    out.containers.push({ sel: selOf(s.el), videoLinks: s.n });
    var card = cardOf(s.el);
    if (card) {
      var attrs = attrValues(card);
      var href = (card.querySelector("a") || {}).href || "";
      var kidAttr = null;
      for (var k in attrs) if (/^\d{14,16}$/.test(attrs[k])) kidAttr = k;
      var bvid = (href.match(/\/video\/(BV[0-9A-Za-z]+)/) || href.match(/bvid=([A-Za-z0-9]+)/) || [])[1] || null;
      out.cards.push({
        cardSel: selOf(card), attrs: attrs, firstHref: href,
        kidAttr: kidAttr, bvid: bvid,
        text: (card.innerText || "").replace(/\s+/g, " ").slice(0, 50)
      });
    }
  });

  // 读取 BHF IndexedDB 样本（确认主脚本已抓取）
  try {
    var db = await new Promise(function (res, rej) {
      var r = indexedDB.open("bhf_history", 1);
      r.onsuccess = function (e) { res(e.target.result); };
      r.onerror = function (e) { rej(e.target.error); };
    });
    var raw = await new Promise(function (res, rej) {
      var t = db.transaction("raw", "readonly").objectStore("raw").getAll();
      t.onsuccess = function (e) { res(e.target.result || []); };
      t.onerror = function (e) { rej(e.target.error); };
    });
    raw.slice(0, 3).forEach(function (r) {
      out.stored.push({ kid: r.kid, bvid: (r.history && r.history.bvid) || r.bvid, uri: r.uri });
    });
    if (!raw.length) out.note = "IndexedDB 暂无数据：请确认主脚本已抓取（顶栏「已抓取 N 条」，N>0）";
  } catch (e) {
    out.note = "读 IndexedDB 失败：" + String(e) + "（主脚本是否安装并抓到数据？）";
  }

  var txt = JSON.stringify(out, null, 2);
  console.log("=====BHF-DOM-REPORT-BEGIN=====");
  console.log(txt);
  console.log("=====BHF-DOM-REPORT-END=====");
  try { window.prompt("复制以下 BHF DOM 报告贴给开发者：", txt); } catch (e) {}
})();
