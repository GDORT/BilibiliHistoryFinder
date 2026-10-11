// ==UserScript==
// @name         B站历史增强 · 本地分类 / 打标 / 续看筛选（纯本地）
// @namespace    https://github.com/GDORT/BilibiliHistoryFinder
// @version      0.4.1
// @description  在 B站历史页旁听你自己的观看记录，在浏览器本地完成分类、打标、筛选与导出。不需要后端、不读 Cookie、不回写 B站、不发送任何数据到外部。
// @author       GDORT
// @license      MIT
// @match        https://www.bilibili.com/history*
// @match        https://*.bilibili.com/history*
// @run-at       document-start
// @grant        none
// @supportURL   https://github.com/GDORT/BilibiliHistoryFinder/issues
// @homepageURL  https://github.com/GDORT/BilibiliHistoryFinder
// ==/UserScript==

/* ============================================================================
 * BHF core —— 油猴阶段纯逻辑（M0/M1/M2/M3/M4/M5 共用，可独立单测）
 *
 * 设计：挂载到 window.BHF。本文件**不依赖任何页面 DOM**（Storage 用 IndexedDB、
 * 但调用是 Promise，测试在 headless Chromium 里跑）。引擎部分**逐函数**移植自
 * src/engine.py，语义严格一致（含 #41 duration 守卫、#42 分段排序）。
 *
 * 红线：raw 层**原样**存整条响应；derived 层只做规范化；不回写 B站。
 * ==========================================================================*/
(function (global) {
  "use strict";
  var BHF = global.BHF = global.BHF || {};

  /* ============================ 0. 工具 ============================ */
  var Util = {
    now: function () { return Math.floor(Date.now() / 1000); },
    clone: function (o) {
      if (o === null || typeof o !== "object") return o;
      return JSON.parse(JSON.stringify(o));
    },
    escapeHtml: function (s) {
      if (s == null) return "";
      return String(s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    },
    fmtDur: function (sec) {
      sec = Number(sec) || 0;
      var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
      if (h > 0) return h + ":" + (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
      return m + ":" + (s < 10 ? "0" : "") + s;
    },
    pctOf: function (rec) {
      var p = rec.progress, d = rec.duration;
      if (p === -1) return 1;
      if (p == null || !d || d <= 0) return null;
      return p / d;
    }
  };
  BHF.Util = Util;

  /* ============================ 1. 引擎（M1，移植自 engine.py） ============================ */
  var ALL_BUSINESS = ["archive", "pgc", "article", "live"];

  function default_rules() {
    return { "rules": [
      {
        "id": "default", "name": "默认续看规则", "active": true, "match": "any",
        "groups": [
          { "label": "接近看完", "action": "auto_skip", "conditions": [ { "field": "progress_pct", "op": ">=", "value": 0.95 } ] },
          { "label": "误触-短暂观看", "action": "auto_skip", "conditions": [ { "field": "progress_pct", "op": "<", "value": 0.03 }, { "field": "progress_sec", "op": "<", "value": 15 } ] },
          { "label": "短视频碎片", "action": "auto_skip", "conditions": [ { "field": "duration", "op": "<", "value": 60 } ] },
          { "label": "直播回放", "action": "auto_skip", "conditions": [ { "field": "business", "op": "in", "value": "live" } ] },
          { "label": "陈旧-久未观看", "action": "stale", "conditions": [ { "field": "view_at_age_days", "op": ">", "value": 90 } ] }
        ]
      }
    ] };
  }

  function get_active_rule(rules) {
    var rs = (rules && rules.rules) || [];
    for (var i = 0; i < rs.length; i++) if (rs[i].active) return rs[i];
    return null;
  }

  function _derived(rec, now) {
    var prog = rec.progress;
    var dur = rec.duration;
    var d = {};
    if (prog === -1) {
      d.progress_pct = 1.0;
      d.progress_sec = dur ? dur : 0;
    } else if (prog != null && dur && dur > 0) {
      d.progress_pct = prog / dur;
      d.progress_sec = prog;
    } else {
      d.progress_pct = null;
      d.progress_sec = (prog != null) ? prog : null;
    }
    var va = rec.view_at;
    d.view_at_age_days = (va) ? (now - va) / 86400 : null;
    return d;
  }

  function _gv(rec, key, fallback) {
    var v = rec[key];
    if (v === undefined) v = null;
    if (fallback !== undefined && (v === null || v === "")) return fallback;
    return v;
  }

  function eval_condition(rec, cond, now, ctx) {
    var field = cond.field, op = cond.op, val = cond.value;
    var der = _derived(rec, now);
    var cur;
    if (field === "progress_pct") cur = der.progress_pct;
    else if (field === "progress_sec") cur = der.progress_sec;
    else if (field === "view_at_age_days") cur = der.view_at_age_days;
    else if (field === "business") cur = _gv(rec, "business");
    else if (field === "author_name") cur = _gv(rec, "author_name", "");
    else if (field === "author_mid") cur = _gv(rec, "author_mid", "");
    else if (field === "title") cur = _gv(rec, "title", "");
    else if (field === "duration") cur = _gv(rec, "duration");
    else if (field === "archived_only") cur = _gv(rec, "archived_only");
    else if (field === "tag_name") cur = _gv(rec, "tag_name", "");
    else if (field === "remark") cur = _gv(rec, "remark", "");
    else if (field === "main_category") cur = _gv(rec, "main_category", "");
    else if (field === "is_fav") cur = _gv(rec, "is_fav");
    else if (field === "live_status") cur = _gv(rec, "live_status");
    else if (field === "videos") cur = _gv(rec, "videos");
    else if (field === "progress") cur = _gv(rec, "progress");
    else if (field === "view_at") cur = _gv(rec, "view_at");
    else if (field === "dt") cur = _gv(rec, "dt");
    else cur = null;

    if (op === "exists") return cur !== null && cur !== "";
    if (op === "empty") return cur === null || cur === "";

    // #41 守卫：duration「有值才判」（不依赖 now；刻意置于 exists/empty 之后）
    if (field === "duration" && op !== "exists" && op !== "empty") {
      if (cur === null || cur === undefined) return false;
      var dn;
      try { dn = Number(cur); } catch (e) { return false; }
      if (isNaN(dn) || dn <= 0) return false;   // 缺时长 / 脏数据 → 不参与时长类条件
    }

    if (op === "relative_after" || op === "relative_before") {
      var secs = _parse_duration(val);
      var days = secs / 86400.0;
      var ref, age_days;
      if (field === "view_at") {
        ref = rec.view_at;
        age_days = ref ? (now - ref) / 86400.0 : null;
      } else {
        age_days = (typeof cur === "number") ? cur : null;
      }
      if (age_days === null) return false;
      if (op === "relative_after") return age_days <= days;     // 近 N 天内
      return age_days > days;                                    // relative_before：超过 N 天
    }

    if (op === "regex") {
      try { return new RegExp(String(val)).test(String(cur == null ? "" : cur)); }
      catch (e) { return false; }
    }
    if (op === "contains") return !!cur && inStr(String(cur), String(val));
    if (op === "not_contains") return !(!!cur && inStr(String(cur), String(val)));
    if (op === "in_list") {
      var vals = (ctx && ctx.lists && ctx.lists[String(val)]) || [];
      return arr_contains(vals, cur);
    }
    if (op === "not_in_list") {
      var vals2 = (ctx && ctx.lists && ctx.lists[String(val)]) || [];
      return !arr_contains(vals2, cur);
    }
    if (op === "in" || op === "not_in") {
      var inset;
      if (Array.isArray(val)) inset = val.map(function (v) { return String(v).trim(); }).filter(Boolean);
      else inset = String(val).split(",").map(function (v) { return v.trim(); }).filter(Boolean);
      var hit = (cur !== null && cur !== undefined) ? inset.indexOf(String(cur)) >= 0 : false;
      return op === "in" ? hit : !hit;
    }
    if (op === "==") return cur == val;
    if (op === "!=") return cur != val;
    if (op === "between" || op === "not_between") {
      var parts;
      try { parts = String(val).split("-").map(Number); if (parts.length !== 2) return false; }
      catch (e) { return false; }
      if (isNaN(parts[0]) || isNaN(parts[1])) return false;
      var cv;
      try { cv = Number(cur); } catch (e) { return false; }
      if (isNaN(cv)) return false;
      var inside = parts[0] <= cv && cv <= parts[1];
      return op === "between" ? inside : !inside;
    }
    if (cur === null || cur === undefined) return false;
    try {
      var c = Number(cur), v = Number(val);
      if (op === ">=") return c >= v;
      if (op === "<=") return c <= v;
      if (op === ">") return c > v;
      if (op === "<") return c < v;
    } catch (e) { return false; }
    return false;
  }

  function inStr(hay, needle) { return hay.indexOf(needle) >= 0; }
  function arr_contains(arr, v) { return arr.indexOf(v) >= 0; }

  function eval_group(rec, g, now, ctx) {
    var conds = g.conditions || [];
    for (var i = 0; i < conds.length; i++) {
      if (!eval_condition(rec, conds[i], now, ctx)) return false;
    }
    return true;
  }

  function evaluate_rule(rec, rule, now) {
    if (rec.progress === -1) return null;          // 已看完是固有状态，规则不可纳入
    var groups = rule.groups || [];
    var matched = [];
    for (var i = 0; i < groups.length; i++) if (eval_group(rec, groups[i], now, null)) matched.push(groups[i]);
    if (!matched.length) return null;
    if (rule.match === "all" && matched.length < groups.length) return null;
    var g = matched[0];
    return [g.action || "auto_skip", g.label || ""];
  }

  function validate_rule(rule) {
    var hard = [], soft = [], info = [];
    var groups = rule.groups || [];
    for (var gi = 0; gi < groups.length; gi++) {
      var g = groups[gi];
      var conds = g.conditions || [];
      var byfield = {};
      conds.forEach(function (c) {
        (byfield[c.field] = byfield[c.field] || []).push(c);
      });
      Object.keys(byfield).forEach(function (f) {
        var lst = byfield[f], ge = [], le = [];
        lst.forEach(function (c) {
          try {
            if (c.op === ">=") ge.push(Number(c.value));
            else if (c.op === ">") ge.push(Number(c.value) + 1e-9);
            else if (c.op === "<=") le.push(Number(c.value));
            else if (c.op === "<") le.push(Number(c.value) - 1e-9);
          } catch (e) {}
        });
        ge.forEach(function (a) {
          le.forEach(function (b) {
            if (a >= b) hard.push("分组「" + (g.label || "") + "」同一字段 " + f + " 既要求 ≥" + a + " 又要求 ≤" + b + "，无记录可同时满足（C1 冲突）");
          });
        });
        var lt = lst.filter(function (c) { return c.op === "<"; }).map(function (c) { return Number(c.value); });
        var ge2 = lst.filter(function (c) { return c.op === ">="; }).map(function (c) { return Number(c.value); });
        for (var i = 0; i < lt.length; i++) for (var j = 0; j < lt.length; j++) {
          if (i !== j && lt[i] <= lt[j]) soft.push("分组「" + (g.label || "") + "」" + f + " <" + lt[i] + " 已被 <" + lt[j] + " 包含，后者冗余（C2）");
        }
        for (var i2 = 0; i2 < ge2.length; i2++) for (var j2 = 0; j2 < ge2.length; j2++) {
          if (i2 !== j2 && ge2[i2] >= ge2[j2]) soft.push("分组「" + (g.label || "") + "」" + f + " ≥" + ge2[i2] + " 已包含 ≥" + ge2[j2] + "，后者冗余（C2）");
        }
      });
      conds.forEach(function (c) {
        if (c.field === "progress_pct" || c.field === "progress_sec") {
          try {
            var v = Number(c.value);
            if (c.op === "<=" && v >= 1.0) hard.push("分组「" + (g.label || "") + "」进度条件 " + c.op + v + " 会把「已看完」纳入跳过，违反固有状态不可反转（C3）");
          } catch (e) {}
        }
      });
    }
    return { hard: hard, soft: soft, info: info };
  }

  function classify(records, rule, now) {
    if (now == null) now = Util.now();
    var total = 0, finished = 0, auto_skip = 0, stale = 0, needs = 0;
    var samples = { auto_skip: [], stale: [], needs: [] };
    for (var i = 0; i < records.length; i++) {
      var rec = records[i];
      total++;
      var prog = rec.progress;
      if (prog === -1) { finished++; continue; }
      var res = evaluate_rule(rec, rule, now);
      if (res) {
        var action = res[0], label = res[1];
        if (action === "stale") stale++; else auto_skip++;
        var bucket = (action === "stale") ? "stale" : "auto_skip";
        if (samples[bucket].length < 5) {
          samples[bucket].push({
            kid: rec.kid, bvid: rec.bvid, title: rec.title, business: rec.business,
            progress: prog, duration: rec.duration, view_at: rec.view_at, action: action, label: label
          });
        }
      } else {
        needs++;
        if (samples.needs.length < 5) {
          samples.needs.push({
            kid: rec.kid, bvid: rec.bvid, title: rec.title, business: rec.business,
            progress: prog, duration: rec.duration, view_at: rec.view_at
          });
        }
      }
    }
    return {
      total: total, finished: finished, auto_skip: auto_skip, stale: stale, needs: needs,
      active_rule_id: rule ? rule.id : null, samples: samples
    };
  }

  function rules_hash(rules) {
    return _md5(JSON.stringify(rules));
  }

  function _parse_duration(val) {
    if (val == null) return 0;
    var s = String(val).trim().toLowerCase();
    var mult = { d: 86400, w: 604800, h: 3600, m: 2592000 };
    if (s && mult[s[s.length - 1]]) {
      var n = parseFloat(s.slice(0, -1));
      return isNaN(n) ? 0 : n * mult[s[s.length - 1]];
    }
    var d = parseFloat(s);
    return isNaN(d) ? 0 : d * 86400;
  }

  function evaluate_filter(rec, spec, now, ctx) {
    if (!spec) return true;
    var groups = spec.groups || [];
    var result;
    if (!groups.length) result = true;
    else {
      var gr = groups.map(function (g) { return eval_group(rec, g, now, ctx); });
      var logic = (spec.logic || "AND").toUpperCase();
      if (logic === "OR") result = gr.some(Boolean);
      else if (logic === "NOR") result = !gr.some(Boolean);
      else result = gr.every(Boolean);
    }
    if (spec.negate) result = !result;
    var having = spec.having;
    if (having && result) {
      var gb = having.groupBy;
      var counts = (ctx && ctx.group_counts) || {};
      var key = rec[gb];
      var cnt = counts[key] || 0;
      var v; try { v = Number(having.value || 0); } catch (e) { v = 0; }
      var hop = having.op || ">";
      if (hop === ">") result = cnt > v;
      else if (hop === ">=") result = cnt >= v;
      else if (hop === "<") result = cnt < v;
      else if (hop === "<=") result = cnt <= v;
      else if (hop === "==") result = cnt == v;
      else result = false;
    }
    return result;
  }

  function validate_filter(spec) {
    var hard = [], soft = [];
    if (!spec) return { hard: hard, soft: soft };
    (spec.groups || []).forEach(function (g, gi) {
      var byfield = {};
      (g.conditions || []).forEach(function (c) { (byfield[c.field] = byfield[c.field] || []).push(c); });
      Object.keys(byfield).forEach(function (f) {
        var lst = byfield[f], ge = [], le = [];
        lst.forEach(function (c) {
          try {
            if (c.op === ">=") ge.push(Number(c.value));
            else if (c.op === ">") ge.push(Number(c.value) + 1e-9);
            else if (c.op === "<=") le.push(Number(c.value));
            else if (c.op === "<") le.push(Number(c.value) - 1e-9);
          } catch (e) {}
        });
        ge.forEach(function (a) { le.forEach(function (b) {
          if (a >= b) hard.push("筛选分组 #" + (gi + 1) + " 同字段 " + f + " 既要求 ≥" + a + " 又要求 ≤" + b + "，无记录可同时满足");
        }); });
      });
    });
    return { hard: hard, soft: soft };
  }

  function sort_value(rec, field, now) {
    var der = _derived(rec, now);
    var mapping = {
      view_at: rec.view_at, duration: rec.duration, progress: rec.progress,
      progress_pct: der.progress_pct, progress_sec: der.progress_sec, view_at_age_days: der.view_at_age_days,
      title: rec.title, author_name: rec.author_name, business: rec.business, main_category: rec.main_category
    };
    var v = (field in mapping) ? mapping[field] : rec[field];
    if (v === null || v === undefined) return [1, 0];
    if (typeof v === "string") return [0, v.toLowerCase()];
    try { return [0, Number(v)]; } catch (e) { return [0, 0]; }
  }

  // #42 分段排序：有值段按方向排、缺值段永远追加末尾；原地排序、返回 undefined
  function apply_sort(recs, sort_fields, now) {
    if (!sort_fields) return;
    for (var s = 0; s < sort_fields.length; s++) {
      var sf = sort_fields[s];
      var f = sf.field;
      var rev = (sf.dir || "desc") === "desc";
      var rows = [];
      for (var i = 0; i < recs.length; i++) rows.push([i, recs[i]]);
      var present = rows.filter(function (t) { return sort_value(t[1], f, now)[0] === 0; });
      var missing = rows.filter(function (t) { return sort_value(t[1], f, now)[0] !== 0; });
      var key = function (t) { return sort_value(t[1], f, now); };
      if (rev) present.sort(function (a, b) { var ka = key(a), kb = key(b); return cmp(ka, kb, true); });
      else present.sort(function (a, b) { var ka = key(a), kb = key(b); return cmp(ka, kb, false); });
      missing.sort(function (a, b) { return a[0] - b[0]; });
      var merged = present.concat(missing);
      for (var k = 0; k < merged.length; k++) recs[k] = merged[k][1];
    }
  }
  function cmp(ka, kb, rev) {
    var sign = rev ? -1 : 1;
    if (ka[0] !== kb[0]) return (ka[0] - kb[0]) * sign;
    if (ka[1] < kb[1]) return -1 * sign;
    if (ka[1] > kb[1]) return 1 * sign;
    return 0;
  }

  // 简易 md5（仅用于 rules_hash 一致性，不用于安全）
  function _md5(s) {
    function rot(x, n) { return (x << n) | (x >>> (32 - n)); }
    function add(x, y) { var l = (x & 0xffff) + (y & 0xffff), h = (x >> 16) + (y >> 16) + (l >> 16); return (h << 16) | (l & 0xffff); }
    function cmn(q, a, b, x, s, t) { a = add(add(a, q), add(x, t)); return add(rot(a, s), b); }
    function ff(a, b, c, d, x, s, t) { return cmn((b & c) | ((~b) & d), a, b, x, s, t); }
    function gg(a, b, c, d, x, s, t) { return cmn((b & d) | (c & ~d), a, b, x, s, t); }
    function hh(a, b, c, d, x, s, t) { return cmn(b ^ c ^ d, a, b, x, s, t); }
    function ii(a, b, c, d, x, s, t) { return cmn(c ^ (b | ~d), a, b, x, s, t); }
    function md5cycle(x, k) {
      var a = x[0], b = x[1], c = x[2], d = x[3];
      a = ff(a, b, c, d, k[0], 7, -680876936); d = ff(d, a, b, c, k[1], 12, -389564586); c = ff(c, d, a, b, k[2], 17, 606105819);
      b = ff(b, c, d, a, k[3], 22, -1044525330); a = ff(a, b, c, d, k[4], 7, -176418897); d = ff(d, a, b, c, k[5], 12, 1200080426);
      c = ff(c, d, a, b, k[6], 17, -1473231341); b = ff(b, c, d, a, k[7], 22, -45705983);
      a = ff(a, b, c, d, k[8], 7, 1770035416); d = ff(d, a, b, c, k[9], 12, -1958414417); c = ff(c, d, a, b, k[10], 17, -42063);
      b = ff(b, c, d, a, k[11], 22, -1990404162); a = ff(a, b, c, d, k[12], 7, 1804603682); d = ff(d, a, b, c, k[13], 12, -40341101);
      c = ff(c, d, a, b, k[14], 17, -1502002290); b = ff(b, c, d, a, k[15], 22, 1236535329);
      a = gg(a, b, c, d, k[1], 5, -165796510); d = gg(d, a, b, c, k[6], 9, -1069501632); c = gg(c, d, a, b, k[11], 14, 643717713);
      b = gg(b, c, d, a, k[0], 20, -373897302); a = gg(a, b, c, d, k[5], 5, -701558691); d = gg(d, a, b, c, k[10], 9, 38016083);
      c = gg(c, d, a, b, k[15], 14, -660478335); b = gg(b, c, d, a, k[4], 20, -405537848); a = gg(a, b, c, d, k[9], 5, 568446438);
      d = gg(d, a, b, c, k[14], 9, -1019803690); c = gg(c, d, a, b, k[3], 14, -187363961); b = gg(b, c, d, a, k[8], 20, 1163531501);
      a = gg(a, b, c, d, k[13], 5, -1444681467); d = gg(d, a, b, c, k[2], 9, -51403784); c = gg(c, d, a, b, k[7], 14, 1735328473);
      b = gg(b, c, d, a, k[12], 20, -1926607734); a = hh(a, b, c, d, k[5], 4, -378558); d = hh(d, a, b, c, k[8], 11, -2022574463);
      c = hh(c, d, a, b, k[11], 16, 1839030562); b = hh(b, c, d, a, k[14], 23, -35309556); a = hh(a, b, c, d, k[1], 4, -1530992060);
      d = hh(d, a, b, c, k[4], 11, 1272893353); c = hh(c, d, a, b, k[7], 16, -155497632); b = hh(b, c, d, a, k[10], 23, -1094730640);
      a = hh(a, b, c, d, k[13], 4, 681279174); d = hh(d, a, b, c, k[0], 11, -358537222); c = hh(c, d, a, b, k[3], 16, -722521979);
      b = hh(b, c, d, a, k[6], 23, 76029189); a = hh(a, b, c, d, k[9], 4, -640364487); d = hh(d, a, b, c, k[12], 11, -421815835);
      c = hh(c, d, a, b, k[15], 16, 530742520); b = hh(b, c, d, a, k[2], 23, -995338651);
      a = ii(a, b, c, d, k[0], 6, -198630844); d = ii(d, a, b, c, k[7], 10, 1126891415); c = ii(c, d, a, b, k[14], 15, -1416354905);
      b = ii(b, c, d, a, k[5], 21, -57434055); a = ii(a, b, c, d, k[12], 6, 1700485571); d = ii(d, a, b, c, k[3], 10, -1894986606);
      c = ii(c, d, a, b, k[10], 15, -1051523); b = ii(b, c, d, a, k[1], 21, -2054922799); a = ii(a, b, c, d, k[8], 6, 1873313359);
      d = ii(d, a, b, c, k[15], 10, -30611744); c = ii(c, d, a, b, k[6], 15, -1560198380); b = ii(b, c, d, a, k[13], 21, 1309151649);
      a = ii(a, b, c, d, k[4], 6, -145523070); d = ii(d, a, b, c, k[11], 10, -1120210379); c = ii(c, d, a, b, k[2], 15, 718787259);
      b = ii(b, c, d, a, k[9], 21, -343485551);
      x[0] = add(a, x[0]); x[1] = add(b, x[1]); x[2] = add(c, x[2]); x[3] = add(d, x[3]);
    }
    function md5blk(s) { var md5blks = [], i; for (i = 0; i < 64; i += 4) md5blks[i >> 2] = s.charCodeAt(i) + (s.charCodeAt(i + 1) << 8) + (s.charCodeAt(i + 2) << 16) + (s.charCodeAt(i + 3) << 24); return md5blks; }
    var n = s.length, state = [1732584193, -271733879, -1732584194, 271733878], i;
    for (i = 64; i <= s.length; i += 64) md5cycle(state, md5blk(s.substring(i - 64, i)));
    s = s.substring(i - 64); var tail = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    for (i = 0; i < s.length; i += 1) tail[i >> 2] |= s.charCodeAt(i) << ((i % 4) << 3);
    tail[i >> 2] |= 128 << ((i % 4) << 3);
    if (i > 55) { md5cycle(state, tail); for (i = 0; i < 16; i++) tail[i] = 0; }
    tail[14] = n * 8; md5cycle(state, tail);
    return state.map(function (x) { return (x >>> 0).toString(16).padStart(8, "0"); }).join("");
  }

  BHF.Engine = {
    ALL_BUSINESS: ALL_BUSINESS,
    default_rules: default_rules,
    get_active_rule: get_active_rule,
    _derived: _derived,
    eval_condition: eval_condition,
    eval_group: eval_group,
    evaluate_rule: evaluate_rule,
    validate_rule: validate_rule,
    classify: classify,
    rules_hash: rules_hash,
    _parse_duration: _parse_duration,
    evaluate_filter: evaluate_filter,
    validate_filter: validate_filter,
    sort_value: sort_value,
    apply_sort: apply_sort
  };

  /* ============================ 2. 存储（M0，IndexedDB 双存储） ============================ */
  // raw 原样存整条；derived 规范化。主键 kid。
  var Storage = (function () {
    var DB_NAME = "bhf_history", DB_VER = 1;
    var dbp = null;

    function open() {
      if (dbp) return dbp;
      dbp = new Promise(function (resolve, reject) {
        if (!global.indexedDB) { reject(new Error("indexedDB 不可用")); return; }
        var req = indexedDB.open(DB_NAME, DB_VER);
        req.onupgradeneeded = function (e) {
          var db = e.target.result;
          if (!db.objectStoreNames.contains("raw")) db.createObjectStore("raw", { keyPath: "kid" });
          if (!db.objectStoreNames.contains("derived")) db.createObjectStore("derived", { keyPath: "kid" });
          if (!db.objectStoreNames.contains("meta")) db.createObjectStore("meta", { keyPath: "k" });
        };
        req.onsuccess = function (e) { resolve(e.target.result); };
        req.onerror = function (e) { reject(e.target.error); };
      });
      return dbp;
    }

    function tx(db, stores, mode) {
      return db.transaction(stores, mode);
    }

    // 把一条 raw 历史项规范化为 derived（对照 SCHEMA.md：顶层 + history 子对象合并）
    function derive(raw) {
      var h = raw.history || {};
      var d = {
        kid: raw.kid,
        bvid: h.bvid != null ? h.bvid : raw.bvid,
        oid: h.oid != null ? h.oid : raw.oid,
        cid: h.cid != null ? h.cid : raw.cid,
        epid: h.epid != null ? h.epid : raw.epid,
        business: h.business != null ? h.business : raw.business,
        page: h.page != null ? h.page : raw.page,
        part: h.part != null ? h.part : raw.part,
        dt: h.dt != null ? h.dt : raw.dt,
        title: raw.title != null ? raw.title : "",
        show_title: raw.show_title != null ? raw.show_title : "",
        long_title: raw.long_title != null ? raw.long_title : "",
        cover: raw.cover != null ? raw.cover : "",
        covers: raw.covers != null ? raw.covers : null,
        uri: raw.uri != null ? raw.uri : "",
        author_mid: raw.author_mid != null ? raw.author_mid : raw.authorMid,
        author_name: raw.author_name != null ? raw.author_name : "",
        author_face: raw.author_face != null ? raw.author_face : "",
        duration: (raw.duration != null) ? Number(raw.duration) : 0,
        progress: (raw.progress != null) ? Number(raw.progress) : 0,
        is_finish: raw.is_finish === true || raw.is_finish === 1,
        is_fav: raw.is_fav === true || raw.is_fav === 1,
        view_at: (raw.view_at != null) ? Number(raw.view_at) : 0,
        videos: raw.videos != null ? Number(raw.videos) : null,
        total: raw.total != null ? Number(raw.total) : null,
        current: raw.current != null ? Number(raw.current) : null,
        tag_name: raw.tag_name != null ? raw.tag_name : "",
        new_desc: raw.new_desc != null ? raw.new_desc : "",
        live_status: raw.live_status != null ? Number(raw.live_status) : null,
        badge: raw.badge != null ? raw.badge : ""
      };
      // SCHEMA 关键发现 5：is_finish 直接判定看完 → 归一化为 progress=-1（引擎语义）
      if (d.is_finish) d.progress = -1;
      return d;
    }

    function putItem(raw) {
      return open().then(function (db) {
        return new Promise(function (resolve, reject) {
          var t = tx(db, ["raw", "derived"], "readwrite");
          t.objectStore("raw").put(Util.clone(raw));
          t.objectStore("derived").put(derive(raw));
          t.oncomplete = function () { resolve(raw.kid); };
          t.onerror = function () { reject(t.error); };
        });
      });
    }

    function putBatch(list) {
      if (!list || !list.length) return Promise.resolve(0);
      return open().then(function (db) {
        return new Promise(function (resolve, reject) {
          var t = tx(db, ["raw", "derived"], "readwrite");
          list.forEach(function (r) { t.objectStore("raw").put(Util.clone(r)); t.objectStore("derived").put(derive(r)); });
          t.oncomplete = function () { resolve(list.length); };
          t.onerror = function () { reject(t.error); };
        });
      });
    }

    function getAllRaw() {
      return open().then(function (db) {
        return new Promise(function (res, rej) {
          var r = tx(db, "raw", "readonly").objectStore("raw").getAll();
          r.onsuccess = function () { res(r.result); }; r.onerror = function () { rej(r.error); };
        });
      });
    }
    function getAllDerived() {
      return open().then(function (db) {
        return new Promise(function (res, rej) {
          var r = tx(db, "derived", "readonly").objectStore("derived").getAll();
          r.onsuccess = function () { res(r.result); }; r.onerror = function () { rej(r.error); };
        });
      });
    }
    function getByKid(kid) {
      return open().then(function (db) {
        return new Promise(function (res, rej) {
          var r = tx(db, "derived", "readonly").objectStore("derived").get(kid);
          r.onsuccess = function () { res(r.result); }; r.onerror = function () { rej(r.error); };
        });
      });
    }
    function count() {
      return open().then(function (db) {
        return new Promise(function (res, rej) {
          var r = tx(db, "derived", "readonly").objectStore("derived").count();
          r.onsuccess = function () { res(r.result); }; r.onerror = function () { rej(r.error); };
        });
      });
    }
    function setMeta(k, v) {
      return open().then(function (db) {
        return new Promise(function (res, rej) {
          var t = tx(db, "meta", "readwrite"); t.objectStore("meta").put({ k: k, v: v });
          t.oncomplete = function () { res(true); }; t.onerror = function () { rej(t.error); };
        });
      });
    }
    function getMeta(k) {
      return open().then(function (db) {
        return new Promise(function (res, rej) {
          var r = tx(db, "meta", "readonly").objectStore("meta").get(k);
          r.onsuccess = function () { res(r.result ? r.result.v : null); }; r.onerror = function () { rej(r.error); };
        });
      });
    }
    function clear() {
      dbp = null;
      return open().then(function (db) {
        return new Promise(function (res, rej) {
          var t = tx(db, ["raw", "derived", "meta"], "readwrite");
          t.objectStore("raw").clear(); t.objectStore("derived").clear(); t.objectStore("meta").clear();
          t.oncomplete = function () { res(true); }; t.onerror = function () { rej(t.error); };
        });
      });
    }

    return {
      open: open, derive: derive, putItem: putItem, putBatch: putBatch,
      getAllRaw: getAllRaw, getAllDerived: getAllDerived, getByKid: getByKid,
      count: count, setMeta: setMeta, getMeta: getMeta, clear: clear
    };
  })();
  BHF.Storage = Storage;

  /* ============================ 3. 规则（M4，localStorage 持久化） ============================ */
  var Rules = {
    KEY: "bhf_rules",
    load: function () {
      try {
        var raw = global.localStorage ? localStorage.getItem(this.KEY) : null;
        if (!raw) return BHF.Engine.default_rules();
        var d = JSON.parse(raw);
        if (!d || !Array.isArray(d.rules)) return BHF.Engine.default_rules();
        if (!BHF.Engine.get_active_rule(d)) d.rules[0].active = true;
        return d;
      } catch (e) { return BHF.Engine.default_rules(); }
    },
    save: function (rules) {
      try { if (global.localStorage) localStorage.setItem(this.KEY, JSON.stringify(rules)); return true; }
      catch (e) { return false; }
    },
    validate: function (rule) { return BHF.Engine.validate_rule(rule); },
    hash: function (rules) { return BHF.Engine.rules_hash(rules); },
    reset: function () { var d = BHF.Engine.default_rules(); this.save(d); return d; }
  };
  BHF.Rules = Rules;

  /* ============================ 4. Overlay（M3，通用注入逻辑） ============================ */
  // 不直接操作特定页面 DOM——由运行时传入选择器。这里提供纯函数：生成 badge HTML、把分类结果映射到记录。
  var Overlay = {
    // 用户手动取消/恢复的覆盖表（kid → true 表示"已恢复为 needs"）；持久化在 localStorage
    _overrides: {},
    loadOverrides: function () {
      try {
        var raw = global.localStorage ? localStorage.getItem("bhf_overrides") : null;
        this._overrides = raw ? JSON.parse(raw) || {} : {};
      } catch (e) { this._overrides = {}; }
      return this._overrides;
    },
    saveOverrides: function () {
      try { if (global.localStorage) localStorage.setItem("bhf_overrides", JSON.stringify(this._overrides)); return true; }
      catch (e) { return false; }
    },
    // 点击角标切换覆盖：已覆盖→撤销（恢复规则判定）；未覆盖→标记 needs
    cancel: function (kid) {
      if (this._overrides[kid]) delete this._overrides[kid];
      else this._overrides[kid] = true;
      this.saveOverrides();
    },

    /* ---- DOM→kid 解析（真实页卡片不带 kid 属性时，按链接反查） ----
     * 依据实测：卡片只有 <a href>；库里 uri 可能带 scheme/www/查询串，同一视频可有多条记录
     * （同一 uri 多个 kid）。故按 uri → path → bvid 三级匹配，并用「桶＋按 view_at 倒序消费」
     * 保证同一 uri 的多条记录依次对应到各张卡片。
     */
    _normUri: function (u) {
      if (!u) return "";
      var s = String(u).trim();
      s = s.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "");
      s = s.replace(/^www\./i, "");
      s = s.split("#")[0].split("?")[0];
      return s.replace(/\/+$/, "");
    },
    _pathKey: function (u) {
      var s = this._normUri(u);
      var m = s.match(/\/(?:video|live|bangumi|opus|read|festival)\/.*$/i);
      return m ? m[0] : "";
    },
    // 由 derived 映射构建一个 resolver(card)→kid（含消费状态；同一 resolver 供一次扫描用）
    buildResolver: function (records) {
      var byUri = {}, byBvid = {}, used = {};
      function push(map, key, rec) { if (key) (map[key] = map[key] || []).push(rec); }
      var all = [];
      for (var k in records) if (records.hasOwnProperty(k)) all.push(records[k]);
      all.sort(function (a, b) { return (b.view_at || 0) - (a.view_at || 0); });   // 新→旧
      all.forEach(function (rec) {
        push(byUri, Overlay._normUri(rec.uri), rec);
        push(byUri, Overlay._pathKey(rec.uri), rec);
        push(byBvid, rec.bvid, rec);
      });
      function take(map, key) {
        var arr = map[key];
        if (!arr || !arr.length) return null;
        var n = used[key] || 0;
        if (n >= arr.length) return null;                     // 桶已耗尽 → 不再匹配（防重复打标）
        used[key] = n + 1;
        return arr[n].kid;
      }
      return function (card) {
        if (!card || !card.querySelectorAll) return null;
        var as = card.querySelectorAll("a[href]");
        for (var i = 0; i < as.length; i++) {
          var href = as[i].href || as[i].getAttribute("href") || "";
          if (!href) continue;
          var kid = take(byUri, Overlay._normUri(href));
          if (kid) return kid;
          kid = take(byUri, Overlay._pathKey(href));
          if (kid) return kid;
          var m = String(href).match(/\/video\/(BV[0-9A-Za-z]+)/i) || String(href).match(/[?&]bvid=(BV[0-9A-Za-z]+)/i);
          if (m) { kid = take(byBvid, m[1]); if (kid) return kid; }
        }
        return null;                                          // 护栏：卡片无可解析链接 → 不打角标
      };
    },

    // 根据分类结果给一条 derived 记录打标签（纯函数）
    classifyOne: function (rec, rule, now) {
      if (this._overrides[rec.kid]) return { state: "needs", overridden: true };
      var r = BHF.Engine.evaluate_rule(rec, rule, now);
      if (rec.progress === -1) return { state: "finished" };
      if (r) return { state: r[0] === "stale" ? "stale" : "auto", reason: r[1], action: r[0] };
      return { state: "needs" };
    },

    // 统计分类结果（纯函数）：给顶栏「分类分布」与抽屉「分类视图」用。
    // 返回 { total, finished, auto_skip, stale, needs, groups:{标签:命中数} }
    // groups 逐条规则命中数 —— 用于判断「规则没命中」还是「规则命中了但没显示」。
    summarize: function (records, rule, now) {
      var list = [];
      if (Array.isArray(records)) list = records;
      else { for (var k in records) if (records.hasOwnProperty(k)) list.push(records[k]); }
      var gs = (rule && rule.groups) || [];
      var out = { total: list.length, finished: 0, auto_skip: 0, stale: 0, needs: 0, groups: {} };
      for (var gi = 0; gi < gs.length; gi++) out.groups[gs[gi].label || ("#" + gi)] = 0;
      for (var i = 0; i < list.length; i++) {
        var rec = list[i];
        if (rec.progress === -1) { out.finished++; continue; }
        if (this._overrides[rec.kid]) { out.needs++; continue; }   // 已手动取消 → 归待观看
        var hit = null;
        for (var gj = 0; gj < gs.length; gj++) {
          if (BHF.Engine.eval_group(rec, gs[gj], now, null)) {
            out.groups[gs[gj].label || ("#" + gj)]++;
            if (!hit) hit = gs[gj];
          }
        }
        if (hit) { if (hit.action === "stale") out.stale++; else out.auto_skip++; }
        else out.needs++;
      }
      return out;
    },

    // 分类视图：把某一分类的记录列成清单（不隐藏/不改动原页面，纯新增 DOM）
    // bucket: finished | auto | stale | needs | all
    viewRows: function (records, rule, now, bucket) {
      var list = [];
      if (Array.isArray(records)) list = records;
      else { for (var k in records) if (records.hasOwnProperty(k)) list.push(records[k]); }
      var rows = [];
      for (var i = 0; i < list.length; i++) {
        var rec = list[i], info = this.classifyOne(rec, rule, now);
        if (bucket && bucket !== "all" && info.state !== bucket) continue;
        rows.push({
          kid: rec.kid, title: rec.title || rec.long_title || "(无标题)",
          author: rec.author_name || "", uri: rec.uri || "",
          progress: rec.progress, duration: rec.duration, view_at: rec.view_at,
          pct: Util.pctOf(rec), state: info.state, label: info.reason || ""
        });
      }
      rows.sort(function (a, b) { return (b.view_at || 0) - (a.view_at || 0); });
      return rows;
    },
    // 生成 badge HTML（沿用 web 的视觉语言；cancelable 角标可点击取消，recoverable 可点回）
    badgeHtml: function (info) {
      if (info.state === "finished") return '<span class="bhf-badge bhf-done">已看完</span>';
      if (info.state === "auto") {
        var lbl = info.reason || "";
        if (info.action === "stale") return '<span class="bhf-badge bhf-stale cancelable" title="按规则自动搁置：点击取消">已搁置·' + Util.escapeHtml(lbl) + '</span>';
        return '<span class="bhf-badge bhf-auto cancelable" title="按规则自动跳过：点击取消">已跳过·' + Util.escapeHtml(lbl) + '</span>';
      }
      if (info.state === "stale") return '<span class="bhf-badge bhf-stale cancelable" title="按规则自动搁置：点击取消">已搁置·' + Util.escapeHtml(info.reason || "") + '</span>';
      if (info.state === "needs") {
        if (info.overridden) return '<span class="bhf-badge bhf-recovered recoverable" title="已取消自动标记：点击恢复规则判定">已恢复</span>';
        return "";
      }
      return "";
    },

    // 扫描容器里所有卡片，给每张打角标。返回本次打标的 {kid, info} 列表。
    // opts: { container, cardSel, kidAttr='data-kid', kidFromCard=null(函数 card→kid),
    //         records(映射 kid→derived), rule, now, badgeClass='bhf-badge', anchorSel=null, onChange }
    // 优先级：kidFromCard（自定义提取，应对页面不直接暴露 data-kid 的情况）> kidAttr。
    // 负向护栏：卡片缺 kid / 记录在库中没有 → 跳过不打角标、绝不崩。
    scan: function (opts) {
      var container = opts.container, cardSel = opts.cardSel, kidAttr = opts.kidAttr || "data-kid";
      var kidFromCard = (typeof opts.kidFromCard === "function") ? opts.kidFromCard : null;
      var resolve = (typeof opts.resolve === "function") ? opts.resolve : null;
      var records = opts.records || {}, rule = opts.rule, now = opts.now;
      var badgeClass = opts.badgeClass || "bhf-badge", anchorSel = opts.anchorSel || null;
      if (!container || !cardSel || !global.document) return [];
      var cards = container.querySelectorAll(cardSel);
      var results = [];
      for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var kid = null;
        if (kidFromCard) kid = kidFromCard(card, { bvidIndex: opts.bvidIndex });
        if (kid === null || kid === undefined || kid === "") kid = resolve ? resolve(card) : null;
        if (kid === null || kid === undefined || kid === "") kid = card.getAttribute(kidAttr);
        if (kid == null || kid === "") continue;                 // 护栏：缺 kid
        var old = card.querySelector("." + badgeClass + "-wrap"); // 重扫重建，保证覆盖生效
        if (old && old.parentNode) old.parentNode.removeChild(old);
        var rec = records[kid];
        if (!rec) continue;                                       // 护栏：库里没有这条
        var info = this.classifyOne(rec, rule, now);
        var html = this.badgeHtml(info);
        if (!html) continue;
        var wrap = document.createElement("span");
        wrap.className = badgeClass + "-wrap";
        wrap.setAttribute("data-bhf-kid", kid);
        wrap.innerHTML = html;
        var anchor = anchorSel ? card.querySelector(anchorSel) : null;
        if (!anchor) anchor = card;
        anchor.appendChild(wrap);
        var b = wrap.firstChild;
        if (b && b.classList && (b.classList.contains("cancelable") || b.classList.contains("recoverable"))) {
          (function (k, el) {
            el.addEventListener("click", function (e) {
              try { e.stopPropagation(); } catch (x) {}
              Overlay.cancel(k);
              if (opts.onChange) opts.onChange();
            });
          })(kid, b);
        }
        results.push({ kid: kid, info: info });
      }
      return results;
    },

    // 监听容器子树变化（SPA 滚动分页新增卡片），防抖后自动重扫。
    // 返回 MutationObserver；opts 会被复用（含 _timer / _mo）。
    observe: function (opts) {
      if (!global.MutationObserver || !opts.container) return null;
      if (opts._timer) clearTimeout(opts._timer);
      var mo = new MutationObserver(function () {
        if (opts._timer) clearTimeout(opts._timer);
        opts._timer = setTimeout(function () {
          Overlay.scan(opts);
          if (opts.onChange) opts.onChange();
        }, 50);
      });
      mo.observe(opts.container, { childList: true, subtree: true });
      opts._mo = mo;
      return mo;
    },

    // 高级入口：从 IndexedDB 读派生库 → 构图 → 扫描 → 可选 observe。
    // opts: { container(元素或元素数组), cardSel, kidAttr, kidFromCard, resolve, anchorSel, rule, now, autoObserve=true }
    bootstrap: function (opts) {
      this.loadOverrides();
      // ⚠️ Rules.load() 是包装对象 {rules:[...]}；引擎要的是「激活的那条规则」，必须拆包。
      //    漏拆 → evaluate_rule 看不到 groups → 除已看完外全部判 needs → 页面零角标。
      var rule = opts.rule || BHF.Engine.get_active_rule(BHF.Rules.load());
      var now = (opts.now != null) ? opts.now : BHF.Util.now();
      var self = this;
      return BHF.Storage.getAllDerived().then(function (derAll) {
        var records = {};
        var bvidIndex = {};
        for (var i = 0; i < derAll.length; i++) {
          var d = derAll[i];
          records[d.kid] = d;
          if (d.bvid) {                                  // bvid → kid（取最新观看），供 kidFromCard 反向查
            var prev = bvidIndex[d.bvid];
            if (!prev || (d.view_at || 0) >= (prev.view_at || 0)) bvidIndex[d.bvid] = { kid: d.kid, view_at: d.view_at };
          }
        }
        var o = {};
        for (var k in opts) if (opts.hasOwnProperty(k)) o[k] = opts[k];
        o.records = records; o.rule = rule; o.now = now;
        o.kidFromCard = opts.kidFromCard; o.bvidIndex = bvidIndex;
        // 默认构建内建 resolver（DOM 无 kid 属性时按 uri/path/bvid 反查）；每次扫描新建，保证分页可重复扫
        if (!o.kidFromCard && typeof o.resolve !== "function") o.resolve = self.buildResolver(records);
        var res = self.scan(o);
        if (opts.autoObserve !== false && opts.container) self.observe(o);
        return res;
      });
    },

    // 多容器入口：真实历史页可能按日期分成多个 .history-list。共用同一个 resolver，
    // 保证「同一 uri 多条记录」的消费顺序与卡片在 document 中的先后一致。
    // opts: { containers:[元素...], cardSel, ...（其余同 bootstrap）}
    bootstrapAll: function (opts) {
      var list = [];
      var cs = opts.containers || (opts.container ? [].slice.call(opts.container) : []);
      for (var i = 0; i < cs.length; i++) if (cs[i]) list.push(cs[i]);
      if (!list.length) return Promise.resolve([]);
      this.loadOverrides();
      var rule = opts.rule || BHF.Engine.get_active_rule(BHF.Rules.load());   // 同 bootstrap：必须拆包
      var now = (opts.now != null) ? opts.now : BHF.Util.now();
      var self = this;
      return BHF.Storage.getAllDerived().then(function (derAll) {
        var records = {}, bvidIndex = {};
        for (var i2 = 0; i2 < derAll.length; i2++) {
          var d = derAll[i2];
          records[d.kid] = d;
          if (d.bvid && (!bvidIndex[d.bvid] || (d.view_at || 0) >= (bvidIndex[d.bvid].view_at || 0)))
            bvidIndex[d.bvid] = { kid: d.kid, view_at: d.view_at };
        }
        // 共享一个 resolver：所有容器按 document 顺序消费「同一 uri 的多条记录」
        var shared = (typeof opts.resolve === "function") ? opts.resolve
                   : (!opts.kidFromCard ? self.buildResolver(records) : null);
        var merged = [];
        for (var c = 0; c < list.length; c++) {
          var o = { container: list[c], records: records, rule: rule, now: now,
                    cardSel: opts.cardSel, kidAttr: opts.kidAttr, kidFromCard: opts.kidFromCard,
                    bvidIndex: bvidIndex, resolve: shared, anchorSel: opts.anchorSel,
                    badgeClass: opts.badgeClass, onChange: opts.onChange };
          merged = merged.concat(self.scan(o));
          if (opts.autoObserve !== false) self.observe(o);
        }
        return merged;
      });
    }
  };
  BHF.Overlay = Overlay;

  /* ============================ 5. 导出（M5） ============================ */
  var Export = {
    toJSON: function (records) {
      return JSON.stringify({ exported_at: Util.now(), count: records.length, records: records }, null, 2);
    },
    download: function (records, filename) {
      var blob = new Blob([this.toJSON(records)], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url; a.download = filename || ("bhf-export-" + Util.now() + ".json");
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }
  };
  BHF.Export = Export;

  /* ============================ 6. UI（M4，规则抽屉） ============================ */
  // 纯 DOM 构造 ＋ 逻辑绑定，不依赖具体页面。样式由 runtime.js 注入（这里只搭结构）。
  var UI = {
    // 可视化规则编辑器所需的字段/运算符/动作元数据（与引擎 eval_condition 支持集一致）
    FIELD_LABELS: {
      progress_pct: "观看百分比(0-1)", progress_sec: "观看秒数", view_at_age_days: "距今天数",
      duration: "时长(秒)", business: "内容类型", author_name: "UP主名", author_mid: "UP主ID",
      title: "标题", tag_name: "标签", remark: "备注", main_category: "主分区",
      is_fav: "已收藏", archived_only: "仅归档", live_status: "直播状态", videos: "分P总数",
      progress: "进度", view_at: "观看时间", dt: "设备类型"
    },
    OP_LABELS: {
      ">=": "≥", "<=": "≤", ">": ">", "<": "<", "==": "等于", "!=": "不等于",
      between: "介于", not_between: "不介于", in: "属于", not_in: "不属于",
      in_list: "在名单", not_in_list: "不在名单", contains: "包含", not_contains: "不包含",
      regex: "正则匹配", exists: "有值", empty: "为空",
      relative_after: "近N天内", relative_before: "超过N天"
    },
    ACTION_LABELS: { auto_skip: "自动跳过", stale: "自动搁置" },
    NUMERIC_FIELDS: ["progress_pct", "progress_sec", "view_at_age_days", "duration",
                      "is_fav", "live_status", "videos", "progress", "view_at"],

    // 校验整份规则：取激活规则交给引擎 validate_rule
    validateRules: function (rules) {
      var r = BHF.Engine.get_active_rule(rules);
      if (!r) return { hard: ["无激活规则"], soft: [] };
      return BHF.Engine.validate_rule(r);
    },
    /* ---- 悬浮面板显隐（M6 追加）------------------------------------
     * 需求：两个悬浮面板（分类视图 / 规则抽屉）**默认关闭**，不遮挡页面；
     *      关闭后要能重新打开（顶栏按钮切换），且「关掉的意图」跨刷新保留。
     * 实现：显隐状态存 localStorage（键 bhf.ui.panels），不用 IndexedDB —— 它是
     *      UI 偏好而非数据，需求上要「即时、不阻塞」。
     * ⚠️ 顶部 toolbar 上按钮的旧写法 `display === "none" || !display` 在
     *    「从未设置过 style.display」时恒为 true ⇒ 点一次看似没反应，实为 bug。已改由本对象统一管理。
     */
    Panel: {
      KEY: "bhf.ui.panels",
      _read: function () {
        try {
          var raw = global.localStorage && global.localStorage.getItem(this.KEY);
          var o = raw ? JSON.parse(raw) : {};
          return (o && typeof o === "object") ? o : {};
        } catch (e) { return {}; }
      },
      _write: function (o) {
        try { if (global.localStorage) global.localStorage.setItem(this.KEY, JSON.stringify(o || {})); }
        catch (e) {}
      },
      // 读取持久化状态；**默认 false**（= 默认关闭）
      isOpen: function (name) { return this._read()[name] === true; },
      setOpen: function (name, open) {
        var s = this._read(); s[name] = !!open; this._write(s);
        return !!open;
      },
      // 按持久化状态落地显隐
      apply: function (el, name) {
        if (!el) return false;
        var open = this.isOpen(name);
        el.style.display = open ? "" : "none";
        return open;
      },
      // 切换并写回，返回切换后的可见性
      toggle: function (el, name, onToggle) {
        var next = !this.isOpen(name);
        this.setOpen(name, next);
        if (el) el.style.display = next ? "" : "none";
        if (onToggle) onToggle(next, el);
        return next;
      },
      // 给面板里的 .bhf-close 绑「关闭并记住」；返回解绑函数
      bindClose: function (el, name) {
        if (!el) return function () {};
        var self = this;
        var h = function (e) {
          if (e && e.stopPropagation) e.stopPropagation();
          self.setOpen(name, false);
          el.style.display = "none";
        };
        var btns = el.querySelectorAll(".bhf-close");
        Array.prototype.forEach.call(btns, function (b) { b.addEventListener("click", h); });
        return h;
      }
    },

    // 挂载规则抽屉。opts: { mount, getRecords, onExport, filename }
    // 返回抽屉 DOM。save/reset/export 分别绑定 BHF.Rules 持久化 与 BHF.Export 导出。
    // opts.openIfStored：是否按 Panel 持久化状态决定初始显隐（默认 true ⇒ 默认关闭）。
    mountDrawer: function (opts) {
      opts = opts || {};
      var mount = opts.mount || (global.document ? global.document.body : null);
      if (!mount) return null;
      var drawer = global.document.createElement("div");
      drawer.className = "bhf-drawer";
      drawer.setAttribute("data-bhf-drawer", "1");
      drawer.innerHTML =
        '<div class="bhf-drawer-head">BHF 规则' +
          '<button class="bhf-close" type="button" title="关闭">×</button></div>' +
        '<textarea class="bhf-rule-json" spellcheck="false"></textarea>' +
        '<div class="bhf-rule-msg" role="status"></div>' +
        '<div class="bhf-drawer-actions">' +
          '<button class="bhf-save" type="button">保存</button>' +
          '<button class="bhf-reset" type="button">重置默认</button>' +
          '<button class="bhf-export" type="button">导出</button>' +
        '</div>';
      mount.appendChild(drawer);
      var doc = global.document;
      var ta = drawer.querySelector(".bhf-rule-json");
      var msg = drawer.querySelector(".bhf-rule-msg");
      var self = this;
      function refresh() {
        var rules = BHF.Rules.load();
        ta.value = JSON.stringify(rules, null, 2);
        var v = self.validateRules(rules);
        msg.textContent = v.hard.length
          ? ("硬冲突：" + v.hard.join("；"))
          : (v.soft.length ? ("提醒：" + v.soft.join("；")) : "规则有效");
        msg.className = "bhf-rule-msg " + (v.hard.length ? "bhf-msg-hard"
          : (v.soft.length ? "bhf-msg-soft" : "bhf-msg-ok"));
        return rules;
      }
      drawer.__refresh = refresh;
      refresh();
      drawer.querySelector(".bhf-save").addEventListener("click", function () {
        try {
          var parsed = JSON.parse(ta.value);
          var v = self.validateRules(parsed);
          if (v.hard.length) {
            msg.textContent = "硬冲突，未保存：" + v.hard.join("；");
            msg.className = "bhf-rule-msg bhf-msg-hard";
            return false;
          }
          BHF.Rules.save(parsed);
          msg.textContent = v.soft.length ? ("已保存（提醒：" + v.soft.join("；") + "）") : "已保存";
          msg.className = "bhf-rule-msg bhf-msg-ok";
          return true;
        } catch (e) {
          msg.textContent = "JSON 解析失败：" + (e && e.message ? e.message : e);
          msg.className = "bhf-rule-msg bhf-msg-hard";
          return false;
        }
      });
      drawer.querySelector(".bhf-reset").addEventListener("click", function () {
        BHF.Rules.reset();
        refresh();
      });
      this.Panel.bindClose(drawer, "rules");
      // 默认关闭（除非上次的显隐偏好是"打开"）
      if (opts.openIfStored !== false) this.Panel.apply(drawer, "rules");
      drawer.querySelector(".bhf-export").addEventListener("click", function () {
        var recs = opts.getRecords ? opts.getRecords() : [];
        if (opts.onExport) opts.onExport(recs);
        else BHF.Export.download(recs, opts.filename);
      });
      return drawer;
    },

    // 分类视图面板（README 声明的「筛选入口」）：按分类列出记录，纯新增 DOM，不隐藏/改动原卡片。
    // opts: { mount, getRecords(→数组), rule, now, onOpen(uri) }
    mountViews: function (opts) {
      opts = opts || {};
      var mount = opts.mount || (global.document ? global.document.body : null);
      if (!mount) return null;
      var doc = global.document;
      var panel = doc.createElement("div");
      panel.className = "bhf-views";
      panel.setAttribute("data-bhf-views", "1");
      var BUCKETS = [
        ["all", "全部"], ["finished", "已看完"], ["auto", "已跳过"],
        ["stale", "已搁置"], ["needs", "待观看"]
      ];
      var cur = "all";
      var head = '<div class="bhf-views-head">分类视图<button class="bhf-close" type="button" title="关闭">×</button></div>';
      var tabs = '<div class="bhf-views-tabs">' +
        BUCKETS.map(function (b) {
          return '<button type="button" class="bhf-vtab" data-b="' + b[0] + '">' + b[1] + '</button>';
        }).join("") + '</div>';
      var list = doc.createElement("div");
      list.className = "bhf-views-list";
      panel.innerHTML = head + tabs;
      panel.appendChild(list);
      mount.appendChild(panel);

      // ⚠️ getRecords 可能返回 Promise（如 BHF.Storage.getAllDerived()）或数组。
      //    这里统一兼容：拿到数组后再渲染，否则异步来源会被当成空列表（面板恒显示"暂无记录"）。
      function render() {
        var recs = opts.getRecords ? opts.getRecords() : [];
        if (recs && typeof recs.then === "function") {
          recs.then(function (r) { paint(r || []); }, function () { paint([]); });
          return;
        }
        paint(recs || []);
      }
      function paint(recs) {
        var now = (opts.now != null) ? opts.now : Util.now();
        // ⚠️ Rules.load() 返回的是包装对象 {rules:[...]}；引擎需要的是「激活的那条规则」。
        //    这里必须 get_active_rule 拆包，否则 evaluate_rule 看不到 groups → 全部判 needs。
        var rule = opts.rule || BHF.Engine.get_active_rule(BHF.Rules.load());
        BHF.Overlay.loadOverrides();          // 同步覆盖表，保证「已取消」的行显示为待观看
        var rows = BHF.Overlay.viewRows(recs, rule, now, cur);
        list.innerHTML = "";
        if (!rows.length) {
          list.innerHTML = '<div class="bhf-views-empty">该分类暂无记录</div>';
          return;
        }
        rows.slice(0, 300).forEach(function (r) {
          var it = doc.createElement("div");
          it.className = "bhf-vrow bhf-vs-" + r.state;
          var pctTxt = (r.pct === null || r.pct === undefined)
            ? "进度未知"
            : ("已看 " + Math.round(r.pct * 100) + "%" +
               (r.duration ? " · " + Util.fmtDur(r.duration) : ""));
          var tag = r.state === "finished" ? "已看完"
                  : (r.state === "auto" ? "已跳过" : (r.state === "stale" ? "已搁置" : "待观看"));
          it.innerHTML =
            '<a class="bhf-vtitle" target="_blank" rel="noopener" title="' + Util.escapeHtml(r.title) + '">' +
              Util.escapeHtml(r.title) + '</a>' +
            '<div class="bhf-vmeta"><span class="bhf-vtag">' + tag + '</span>' +
            '<span class="bhf-vpct">' + Util.escapeHtml(pctTxt) + '</span>' +
            (r.label ? '<span class="bhf-vlabel">' + Util.escapeHtml(r.label) + '</span>' : '') +
            (r.author ? '<span class="bhf-vauthor">' + Util.escapeHtml(r.author) + '</span>' : '') +
            '</div>';
          if (r.uri && opts.onOpen !== false) {
            it.querySelector(".bhf-vtitle").addEventListener("click", function (e) {
              if (opts.onOpen) { e.preventDefault(); opts.onOpen(r.uri, r); }
            });
          }
          list.appendChild(it);
        });
        if (rows.length > 300) {
          var more = doc.createElement("div");
          more.className = "bhf-views-empty";
          more.textContent = "仅显示前 300 条（共 " + rows.length + " 条）";
          list.appendChild(more);
        }
      }
      panel.__render = render;
      var self = this;
      // 默认关闭（除非上次的显隐偏好是"打开"）
      if (opts.openIfStored !== false) this.Panel.apply(panel, "views");
      panel.addEventListener("click", function (e) {
        var t = e.target;
        if (t && t.classList && t.classList.contains("bhf-vtab")) {
          cur = t.getAttribute("data-b") || "all";
          Array.prototype.forEach.call(panel.querySelectorAll(".bhf-vtab"), function (b) {
            b.classList.toggle("is-on", b === t);
          });
          render();
        }
      });
      // 关闭按钮：只落显隐，不重置当前 tab（重开时保持上次所在分类）
      self.Panel.bindClose(panel, "views");
      Array.prototype.forEach.call(panel.querySelectorAll(".bhf-vtab"), function (b) {
        if (b.getAttribute("data-b") === "all") b.classList.add("is-on");
      });
      render();
      return panel;
    }
  };
  BHF.UI = UI;

  /* --------- 规则集（多规则 · 单活跃切换 · 可视化编辑，M5 补齐） --------- */
  var RuleSet = {
    // 生成一条空白规则
    blank: function () {
      return {
        id: "r" + Math.floor(Date.now() % 1e7) + "_" + Math.floor(Math.random() * 1000),
        name: "新规则", active: false, match: "any",
        groups: [{ label: "新分组", action: "auto_skip", conditions: [{ field: "progress_pct", op: ">=", value: 0.95 }] }]
      };
    },
    // 复制一份默认规则（作为"预设"起点）
    preset: function () {
      var d = BHF.Engine.default_rules();
      var r = d.rules[0];
      r.id = "p" + Math.floor(Date.now() % 1e7);
      r.name = "预设规则";
      return r;
    },
    // 单活跃切换：只有一条能 active=true。参数为规则包装对象（与 Rules.load() 同形）。
    setActive: function (rules, id) {
      var list = (rules && Array.isArray(rules.rules)) ? rules.rules : (Array.isArray(rules) ? rules : null);
      if (!list) return rules;
      for (var i = 0; i < list.length; i++) list[i].active = (list[i].id === id);
      return rules;
    },

    /* 可视化规则编辑器。
     * opts: { mount, onChange(已保存的 rules), filename }
     * 布局：规则列表（单选激活 ＋ 新增空白/预设 ＋ 删除）＋ 选中规则的分组编辑区
     *      （每组：标签 ＋ 动作 ＋ 条件行[field/op/value] ＋ 增删组/条件）＋ 实时校验条
     */
    mountEditor: function (opts) {
      opts = opts || {};
      var mount = opts.mount || (global.document ? global.document.body : null);
      if (!mount) return null;
      var doc = global.document;
      var self = this;
      var rules = BHF.Rules.load();          // 工作副本
      if (!rules.rules || !rules.rules.length) rules = BHF.Engine.default_rules();

      var root = doc.createElement("div");
      root.className = "bhf-rs";
      root.setAttribute("data-bhf-ruleset", "1");
      mount.appendChild(root);

      function activeIdx() {
        for (var i = 0; i < rules.rules.length; i++) if (rules.rules[i].active) return i;
        return 0;
      }
      function optHtml(map, cur) {
        return Object.keys(map).map(function (k) {
          return '<option value="' + k + '"' + (k === cur ? " selected" : "") + '>' + map[k] + '</option>';
        }).join("");
      }
      function condHtml(g, ci) {
        var c = g.conditions[ci] || {};
        var numeric = (UI.NUMERIC_FIELDS.indexOf(c.field) >= 0);
        var shown = Object.keys(UI.OP_LABELS);
        var val = (c.value === undefined || c.value === null) ? "" : String(c.value);
        return '<div class="bhf-rs-c" data-ci="' + ci + '">' +
          '<select class="bhf-rs-cf" data-act="c-field">' + optHtml(UI.FIELD_LABELS, c.field) + '</select>' +
          '<select class="bhf-rs-co" data-act="c-op">' + shown.map(function (k) {
            return '<option value="' + k + '"' + (k === c.op ? " selected" : "") + '>' + UI.OP_LABELS[k] + '</option>';
          }).join("") + '</select>' +
          '<input class="bhf-rs-cv" data-act="c-value" value="' + UIUtilEscape(val) + '"' +
            (numeric ? ' inputmode="decimal"' : '') + '>' +
          '<button type="button" class="bhf-rs-delc" data-act="del-cond" title="删除条件">×</button>' +
          '</div>';
      }
      function UIUtilEscape(s) { return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;"); }

      function groupHtml(g, gi) {
        var conds = (g.conditions || []).map(function (_, ci) { return condHtml(g, ci); }).join("");
        return '<div class="bhf-rs-g" data-gi="' + gi + '">' +
          '<div class="bhf-rs-gh">' +
            '<input class="bhf-rs-gl" data-act="g-label" value="' + UIUtilEscape(g.label || "") + '" placeholder="分组名">' +
            '<select class="bhf-rs-ga" data-act="g-action">' + optHtml(UI.ACTION_LABELS, g.action || "auto_skip") + '</select>' +
            '<button type="button" class="bhf-rs-delg" data-act="del-group" title="删除分组">删组</button>' +
          '</div>' +
          '<div class="bhf-rs-cs">' + (conds || '<div class="bhf-rs-empty">无条件</div>') + '</div>' +
          '<button type="button" class="bhf-rs-addc" data-act="add-cond">＋ 条件</button>' +
          '</div>';
      }

      function render() {
        var ai = activeIdx();
        var list = rules.rules.map(function (r, i) {
          return '<label class="bhf-rs-item' + (i === ai ? " is-on" : "") + '">' +
            '<input type="radio" name="bhf-rs-active" data-act="active" value="' + r.id + '"' + (i === ai ? " checked" : "") + '>' +
            '<span class="bhf-rs-nm">' + UIUtilEscape(r.name || "(未命名)") + '</span>' +
            '<span class="bhf-rs-cn">' + (r.groups || []).length + ' 组</span>' +
            '</label>';
        }).join("");
        var cur = rules.rules[ai];
        var groups = (cur.groups || []).map(function (g, gi) { return groupHtml(g, gi); }).join("");
        var v = UI.validateRules(rules);
        var msgCls = v.hard.length ? "bhf-msg-hard" : (v.soft.length ? "bhf-msg-soft" : "bhf-msg-ok");
        var msgTxt = v.hard.length ? ("硬冲突：" + v.hard.join("；"))
                   : (v.soft.length ? ("提醒：" + v.soft.join("；")) : "规则有效");
        root.innerHTML =
          '<div class="bhf-rs-sec"><div class="bhf-rs-h">规则（单选＝生效中）</div>' +
            '<div class="bhf-rs-list">' + list + '</div>' +
            '<div class="bhf-rs-adds">' +
              '<button type="button" data-act="add-blank">＋ 新增空白</button>' +
              '<button type="button" data-act="add-preset">＋ 新增预设</button>' +
            '</div>' +
          '</div>' +
          '<div class="bhf-rs-sec"><div class="bhf-rs-h">编辑：' + UIUtilEscape(cur.name || "") + '</div>' +
            '<input class="bhf-rs-name" data-act="r-name" value="' + UIUtilEscape(cur.name || "") + '" placeholder="规则名">' +
            '<div class="bhf-rs-match"><label>多条命中时</label><select data-act="r-match">' +
              '<option value="any"' + (cur.match === "all" ? "" : " selected") + '>任一命中即生效</option>' +
              '<option value="all"' + (cur.match === "all" ? " selected" : "") + '>必须全部命中</option>' +
            '</select></div>' +
            '<div class="bhf-rs-gs">' + groups + '</div>' +
            '<button type="button" class="bhf-rs-addg" data-act="add-group">＋ 新增分组</button>' +
          '</div>' +
          '<div class="bhf-rs-msg ' + msgCls + '">' + UIUtilEscape(msgTxt) + '</div>' +
          '<div class="bhf-rs-foot">' +
            '<button type="button" class="bhf-rs-save" data-act="save">保存并应用</button>' +
            '<button type="button" class="bhf-rs-cancel" data-act="cancel">放弃修改</button>' +
          '</div>';
      }

      // 从 DOM 读回工作副本（用户改的是表单，不是 JSON）
      function collect() {
        var ai = activeIdx();
        var cur = rules.rules[ai];
        var q = function (sel) { return root.querySelector(sel); };
        var nm = q('[data-act="r-name"]'); if (nm) cur.name = nm.value;
        var mm = q('[data-act="r-match"]'); if (mm) cur.match = mm.value;
        Array.prototype.forEach.call(root.querySelectorAll(".bhf-rs-g"), function (gel) {
          var gi = +gel.getAttribute("data-gi");
          var g = cur.groups[gi]; if (!g) return;
          var gl = gel.querySelector('[data-act="g-label"]'); if (gl) g.label = gl.value;
          var ga = gel.querySelector('[data-act="g-action"]'); if (ga) g.action = ga.value;
          var conds = [];
          Array.prototype.forEach.call(gel.querySelectorAll(".bhf-rs-c"), function (cel) {
            var cf = cel.querySelector('[data-act="c-field"]');
            var co = cel.querySelector('[data-act="c-op"]');
            var cv = cel.querySelector('[data-act="c-value"]');
            if (!cf || !co) return;
            var f = cf.value, raw = (cv ? cv.value : "");
            var numeric = (UI.NUMERIC_FIELDS.indexOf(f) >= 0);
            var v = raw;
            if (numeric) { var n = parseFloat(raw); v = isNaN(n) ? raw : n; }
            conds.push({ field: f, op: co.value, value: v });
          });
          g.conditions = conds;
        });
        return rules;
      }

      function commit(rebuild) {
        collect();
        var v = UI.validateRules(rules);
        if (v.hard.length) { render(); return false; }   // 硬冲突：拒存
        BHF.Rules.save(rules);
        if (opts.onChange) opts.onChange(rules);
        if (rebuild) render();
        return true;
      }

      root.addEventListener("click", function (e) {
        var t = e.target.closest ? e.target.closest("[data-act]") : null;
        if (!t) return;
        var act = t.getAttribute("data-act");
        if (act === "save") { commit(true); return; }
        if (act === "cancel") { rules = BHF.Rules.load(); render(); return; }
        if (act === "active") { collect(); RuleSet.setActive(rules, t.value); render(); commit(true); return; }
        if (act === "add-blank") { collect(); var nb = RuleSet.blank(); rules.rules.push(nb); render(); return; }
        if (act === "add-preset") { collect(); var np = RuleSet.preset(); rules.rules.push(np); render(); return; }
        if (act === "add-group") {
          collect();
          rules.rules[activeIdx()].groups.push({ label: "新分组", action: "auto_skip", conditions: [{ field: "progress_pct", op: ">=", value: 0.95 }] });
          render(); return;
        }
        if (act === "add-cond") {
          collect();
          var gel = t.closest(".bhf-rs-g"), gi = +gel.getAttribute("data-gi");
          rules.rules[activeIdx()].groups[gi].conditions.push({ field: "duration", op: "<", value: 60 });
          render(); return;
        }
        if (act === "del-group") {
          collect();
          var gel2 = t.closest(".bhf-rs-g"), gi2 = +gel2.getAttribute("data-gi");
          rules.rules[activeIdx()].groups.splice(gi2, 1);
          render(); return;
        }
        if (act === "del-cond") {
          collect();
          var cel = t.closest(".bhf-rs-c"), gel3 = t.closest(".bhf-rs-g");
          var g3 = +gel3.getAttribute("data-gi"), ci = +cel.getAttribute("data-ci");
          rules.rules[activeIdx()].groups[g3].conditions.splice(ci, 1);
          render(); return;
        }
      });
      root.addEventListener("change", function (e) {
        // 单选激活走 click；其余字段改动即时落回工作副本并重算校验条
        if (e.target && e.target.getAttribute && e.target.getAttribute("data-act") === "active") return;
        collect();
        var v = UI.validateRules(rules);
        var box = root.querySelector(".bhf-rs-msg");
        if (box) {
          box.className = "bhf-rs-msg " + (v.hard.length ? "bhf-msg-hard" : (v.soft.length ? "bhf-msg-soft" : "bhf-msg-ok"));
          box.textContent = v.hard.length ? ("硬冲突：" + v.hard.join("；"))
                          : (v.soft.length ? ("提醒：" + v.soft.join("；")) : "规则有效");
        }
      });

      render();
      return { root: root, render: render, getRules: function () { return rules; } };
    }
  };
  BHF.RuleSet = RuleSet;

  if (typeof module !== "undefined" && module.exports) module.exports = BHF;

})(typeof window !== "undefined" ? window : this);


/* ============================================================================
 * BHF 运行时（M5）：把 core.js 的能力接到真实 B站历史页。
 *
 *  ⚠️ 只读：hook 只"旁听"页面自己发的请求，不回写、不发任何写请求。
 *  ⚠️ 真实 DOM 选择器（CFG）需用户在装上去确认后填写 —— 见下方 TODO。
 *      留 null 时脚本只抓取、不打角标（仍可导出），不会崩。
 *
 *  本文件由 build.py 与 core.js 拼接到 header.js 之后，构成最终的
 *  bili_history.user.js。core.js 必须先于本文件加载（已在 build.py 保证）。
 * ==========================================================================*/
(function () {
  "use strict";
  if (window.__BHF_RT__) return;          // 重复注入保护
  window.__BHF_RT__ = true;
  if (!window.BHF) { console.error("[BHF] core 未加载，运行时退出"); return; }
  var BHF = window.BHF;

  // ── 配置：真实历史页的 DOM 选择器（2026-10-09 由 inspect-dom.js 实测回填） ──
  // 实测结论（见 SCHEMA.md 与 probe-report）：
  //   · 卡片**不带 kid 属性** → 由内建 resolver 按链接 uri → path → bvid 反查（bootstrap 默认启用）
  //   · 库里 kid 并非都是 15 位，且**直播记录无 bvid** → 必须 uri 优先，bvid 仅兜底
  var CFG = {
    feedSelector: "main.history-record",     // 实测容器（392 个视频链接）；用 main 而非按日分组的 .history-list，避免多容器漏抓
    cardSelector: ".bili-video-card",         // 实测卡片（BEM 块名，由 .bili-video-card__cover 推得）
    cardSelectorFallbacks: [".history-card", ".bili-video-card__cover"], // B站改版时的兜底，取首个有卡片的
    kidAttr: "data-kid",                      // 保留：若将来卡片直接暴露 kid 则优先用
    kidFromCard: null,                        // 留空＝用内建 resolver（uri/path/bvid 反查）
    anchorSelector: null                      // 角标插入点：null＝追加到卡片末尾
  };

  var TARGET = "/x/web-interface/history/cursor";

  // ── 抓取计数 / 状态 ───────────────────────────────────────────────
  var state = { captured: 0, lastErr: null };
  var TB = {
    setStatus: function (s) { if (this._st) this._st.textContent = s; },
    setStat: function (s) { if (this._stat) this._stat.innerHTML = s; }
  };

  function noteCount(n) {
    state.captured += n;
    TB.setStatus("已抓取 " + state.captured + " 条");
    refreshStat();
  }

  // 分类分布实时读数：让「规则有没有命中」一眼可见（而不是只能看到零星角标）
  var _statTimer = null;
  function refreshStat() {
    if (_statTimer) clearTimeout(_statTimer);
    _statTimer = setTimeout(function () {
      BHF.Storage.getAllDerived().then(function (all) {
        if (!all.length) { TB.setStat("库里 0 条"); return; }
        var rule = BHF.Engine.get_active_rule(BHF.Rules.load());   // 拆包：summarize 需要激活规则
        var s = BHF.Overlay.summarize(all, rule, null);
        var hitLog = Object.keys(s.groups).map(function (k) {
          return k + " " + s.groups[k];
        }).join(" · ");
        TB.setStat(
          '<b class="bhf-s-fin">' + s.finished + '</b>看完 · ' +
          '<b class="bhf-s-auto">' + s.auto_skip + '</b>跳过 · ' +
          '<b class="bhf-s-stale">' + s.stale + '</b>搁置 · ' +
          '<b class="bhf-s-needs">' + s.needs + '</b>待观看' +
          '<span class="bhf-s-why" title="逐条规则命中数（0 说明规则没命中，不是没显示）">' + hitLog + '</span>'
        );
      }).catch(function () {});
    }, 300);
  }

  // ── 解析并入库（raw 原样存，derived 由 Storage.derive 规范化） ──────────
  function ingest(text) {
    try {
      var d = JSON.parse(text);
      var list = d && d.data && d.data.list;
      if (!list || !list.length) return;
      BHF.Storage.putBatch(list)
        .then(function () { noteCount(list.length); })
        .catch(function (e) { state.lastErr = String(e); console.warn("[BHF] 入库失败", e); });
    } catch (e) { state.lastErr = String(e); }
  }

  // ── hook 1：window.fetch ───────────────────────────────────────────
  try {
    var origFetch = window.fetch;
    if (typeof origFetch === "function") {
      window.fetch = function (input) {
        var url = (typeof input === "string") ? input : (input && input.url) || "";
        var p = origFetch.apply(this, arguments);
        if (url.indexOf(TARGET) !== -1 && p && typeof p.then === "function") {
          p.then(function (res) {
            try { if (res && typeof res.clone === "function") res.clone().text().then(ingest); } catch (e) {}
            return res;
          }).catch(function () {});
        }
        return p;
      };
    }
  } catch (e) { console.warn("[BHF] fetch hook 安装失败", e); }

  // ── hook 2：XMLHttpRequest（兜底） ─────────────────────────────────
  try {
    var OX = window.XMLHttpRequest;
    if (OX && OX.prototype) {
      var oOpen = OX.prototype.open, oSend = OX.prototype.send;
      OX.prototype.open = function () {
        this.__u = String(arguments[1] || "");
        return oOpen.apply(this, arguments);
      };
      OX.prototype.send = function () {
        var self = this, u = this.__u || "";
        if (u.indexOf(TARGET) !== -1) {
          this.addEventListener("readystatechange", function () {
            if (self.readyState === 4) ingest(self.responseText || "");
          });
        }
        return oSend.apply(this, arguments);
      };
    }
  } catch (e) { console.warn("[BHF] XHR hook 安装失败", e); }

  // ── 顶栏 ───────────────────────────────────────────────────────────
  function buildTopbar() {
    try {
      if (!document.body) return;
      var bar = document.createElement("div");
      bar.id = "bhf-topbar";
      bar.innerHTML =
        '<span class="bhf-tb-title">BHF 历史增强</span>' +
        '<span class="bhf-tb-stat" data-role="stat">读取中…</span>' +
        '<span class="bhf-tb-status">启动中…</span>' +
        '<button class="bhf-tb-btn" data-act="views">分类</button>' +
        '<button class="bhf-tb-btn" data-act="rules">规则</button>' +
        '<button class="bhf-tb-btn" data-act="export">导出</button>';
      document.body.appendChild(bar);
      TB._st = bar.querySelector(".bhf-tb-status");
      TB._stat = bar.querySelector('[data-role="stat"]');
      // 悬浮面板统一由 BHF.UI.Panel 管理显隐（默认关闭 + 跨刷新记住）
      bar.querySelector('[data-act="views"]').addEventListener("click", function () {
        var vp = document.querySelector("[data-bhf-views]");
        BHF.UI.Panel.toggle(vp, "views", function (open) {
          if (open && vp && vp.__render) vp.__render();
        });
      });
      bar.querySelector('[data-act="rules"]').addEventListener("click", function () {
        var dr = document.querySelector("[data-bhf-drawer]");
        BHF.UI.Panel.toggle(dr, "rules", function (open) {
          // 打开时刷新：把最新分类结果带进规则校验
          if (open && dr && dr.__refresh) dr.__refresh();
        });
      });
      bar.querySelector('[data-act="export"]').addEventListener("click", function () {
        BHF.Storage.getAllDerived().then(function (all) {
          BHF.Export.download(all, "bhf-history-" + BHF.Util.now() + ".json");
        });
      });
      window.__BHF_TB__ = TB;
    } catch (e) { console.warn("[BHF] 顶栏构建失败", e); }
  }

  // ── overlay 启动 ──────────────────────────────────────────────────────
  function startOverlay() {
    if (!CFG.feedSelector || !CFG.cardSelector) {
      console.info("[BHF] 未配置 feed/card 选择器：只抓取不打标。填 CFG（见 userscript/tools/inspect-dom.js 探测）后重开历史页即可打标");
      return;
    }
    var started = false;
    var tryStart = function () {
      if (started) return true;
      var feeds = document.querySelectorAll(CFG.feedSelector);
      if (!feeds.length) return false;
      // 卡片选择器兜底：取首个能选到卡片的（B站改版时不至于直接失效）
      var sel = CFG.cardSelector;
      var cands = [CFG.cardSelector].concat(CFG.cardSelectorFallbacks || []);
      for (var ci = 0; ci < cands.length; ci++) {
        if (cands[ci] && feeds[0].querySelector(cands[ci])) { sel = cands[ci]; break; }
      }
      started = true;
      BHF.Overlay.bootstrapAll({
        containers: Array.prototype.slice.call(feeds),
        cardSel: sel, kidAttr: CFG.kidAttr, kidFromCard: CFG.kidFromCard,
        anchorSel: CFG.anchorSelector, autoObserve: true,
        onChange: function () { TB.setStatus("已抓取 " + state.captured + " 条 · 角标已更新"); }
      });
      // 规则被改后调用：重跑一次全量扫描，让角标按新规则重打（规则不存 _overrides，安全）
      window.__BHF_RESCAN__ = function () {
        started = false;                     // 允许重新走一次 bootstrapAll（会用新规则）
        tryStart();
      };
      console.info("[BHF] overlay 已启动：容器 " + feeds.length + " 个，卡片选择器 " + sel);
      return true;
    };
    if (!tryStart()) {
      // SPA：容器可能是后加载的，轮询等待（最长 20s）
      var iv = setInterval(function () { if (tryStart()) clearInterval(iv); }, 500);
      setTimeout(function () { clearInterval(iv); }, 20000);
    }
  }

  // ── 样式（自包含，不依赖页面） ─────────────────────────────────────
  function injectStyle() {
    try {
      var css = document.createElement("style");
      css.textContent = [
        "#bhf-topbar{position:fixed;left:0;right:0;top:0;z-index:2147483646;height:34px;",
        "display:flex;align-items:center;gap:10px;padding:0 12px;background:#fff;color:#18191c;",
        "box-shadow:0 1px 6px rgba(0,0,0,.2);font:13px/1 -apple-system,'Microsoft YaHei',sans-serif}",
        ".bhf-tb-title{font-weight:bold;color:#fb7299}",
        ".bhf-tb-status{color:#666}",
        ".bhf-tb-stat{color:#18191c;background:#f6f7f8;border-radius:6px;padding:2px 8px;font-size:12px}",
        ".bhf-tb-stat b{font-weight:700}",
        ".bhf-s-fin{color:#2fa84f}.bhf-s-auto{color:#fb7299}.bhf-s-stale{color:#ff9c2c}.bhf-s-needs{color:#18191c}",
        ".bhf-s-why{margin-left:6px;color:#9499a0;font-size:11px}",
        ".bhf-tb-btn{margin-left:auto;padding:4px 10px;border:0;border-radius:6px;background:#fb7299;color:#fff;cursor:pointer}",
        ".bhf-tb-btn[data-act=rules]{margin-left:0;background:#e3e5e7;color:#18191c}",
        ".bhf-badge{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:6px;font-size:12px;cursor:pointer;vertical-align:middle}",
        ".bhf-badge-wrap{display:inline-block;white-space:nowrap}",
        ".bhf-done{background:#2fa84f;color:#fff}",
        ".bhf-auto{background:#fb7299;color:#fff}",
        ".bhf-stale{background:#ff9c2c;color:#fff}",
        ".bhf-recovered{background:#e3e5e7;color:#18191c}",
        ".bhf-drawer{position:fixed;right:12px;top:44px;z-index:2147483647;width:340px;max-height:80vh;overflow:auto;",
        "background:#fff;color:#18191c;border-radius:10px;padding:12px;box-shadow:0 6px 24px rgba(0,0,0,.3);",
        "font:13px/1.5 -apple-system,'Microsoft YaHei',sans-serif}",
        ".bhf-drawer-head{display:flex;justify-content:space-between;font-weight:bold;margin-bottom:8px}",
        ".bhf-rule-json{width:100%;height:240px;box-sizing:border-box;font:12px/1.4 Consolas,monospace;padding:6px;border:1px solid #ccd;border-radius:6px}",
        ".bhf-rule-msg{margin:6px 0;min-height:18px}",
        ".bhf-msg-ok{color:#2fa84f}.bhf-msg-soft{color:#ff9c2c}.bhf-msg-hard{color:#e5534b}",
        ".bhf-drawer-actions button{margin-right:6px;padding:5px 12px;border:0;border-radius:6px;background:#fb7299;color:#fff;cursor:pointer}",
        ".bhf-drawer-actions .bhf-reset{background:#e3e5e7;color:#18191c}",
        ".bhf-views{position:fixed;left:12px;top:44px;z-index:2147483647;width:360px;max-height:82vh;display:flex;flex-direction:column;",
        "background:#fff;color:#18191c;border-radius:10px;padding:12px;box-shadow:0 6px 24px rgba(0,0,0,.3);",
        "font:13px/1.5 -apple-system,'Microsoft YaHei',sans-serif}",
        ".bhf-views-head{display:flex;justify-content:space-between;font-weight:bold;margin-bottom:8px}",
        ".bhf-views-tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}",
        ".bhf-vtab{padding:3px 8px;border:0;border-radius:6px;background:#f1f2f3;color:#18191c;cursor:pointer;font-size:12px}",
        ".bhf-vtab.is-on{background:#fb7299;color:#fff}",
        ".bhf-views-list{overflow:auto;flex:1}",
        ".bhf-vrow{border-bottom:1px solid #f1f2f3;padding:6px 2px}",
        ".bhf-vtitle{display:block;color:#18191c;text-decoration:none;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
        ".bhf-vtitle:hover{color:#fb7299}",
        ".bhf-vmeta{margin-top:3px;display:flex;gap:6px;flex-wrap:wrap;color:#9499a0;font-size:11px}",
        ".bhf-vtag{padding:0 5px;border-radius:4px;background:#f1f2f3}",
        ".bhf-vs-finished .bhf-vtag{background:#e6f6ea;color:#2fa84f}",
        ".bhf-vs-auto .bhf-vtag{background:#ffeaf1;color:#fb7299}",
        ".bhf-vs-stale .bhf-vtag{background:#fff2e0;color:#ff9c2c}",
        ".bhf-vlabel{color:#9499a0}",
        ".bhf-views-empty{padding:10px 2px;color:#9499a0;text-align:center}",
        ".bhf-rs-wrap{border-bottom:1px solid #eef0f2;padding-bottom:8px;margin-bottom:8px}",
        ".bhf-rs{font:12px/1.5 -apple-system,'Microsoft YaHei',sans-serif}",
        ".bhf-rs-sec{margin-bottom:10px}",
        ".bhf-rs-h{font-weight:bold;margin-bottom:4px}",
        ".bhf-rs-list{border:1px solid #eef0f2;border-radius:6px;max-height:120px;overflow:auto}",
        ".bhf-rs-item{display:flex;align-items:center;gap:6px;padding:4px 6px;cursor:pointer}",
        ".bhf-rs-item.is-on{background:#fff1f5}",
        ".bhf-rs-nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
        ".bhf-rs-cn{color:#9499a0;font-size:11px}",
        ".bhf-rs-adds{margin-top:6px;display:flex;gap:6px}",
        ".bhf-rs-adds button,.bhf-rs-foot button,.bhf-rs-addg,.bhf-rs-addc{flex:1;padding:4px 8px;border:0;border-radius:6px;background:#f1f2f3;color:#18191c;cursor:pointer;font-size:12px}",
        ".bhf-rs-save{background:#fb7299;color:#fff!important;font-weight:bold}",
        ".bhf-rs-name{width:100%;box-sizing:border-box;padding:4px 6px;border:1px solid #dfe3e8;border-radius:6px;margin-bottom:6px}",
        ".bhf-rs-match{margin-bottom:6px;color:#666}",
        ".bhf-rs-match select{padding:2px 4px;margin-left:4px}",
        ".bhf-rs-g{border:1px solid #eef0f2;border-radius:6px;padding:6px;margin-bottom:6px;background:#fafbfc}",
        ".bhf-rs-gh{display:flex;gap:4px;margin-bottom:4px}",
        ".bhf-rs-gl{flex:1;min-width:0;padding:3px 5px;border:1px solid #dfe3e8;border-radius:5px}",
        ".bhf-rs-ga{padding:3px;border:1px solid #dfe3e8;border-radius:5px}",
        ".bhf-rs-delg,.bhf-rs-delc{padding:3px 6px;border:0;border-radius:5px;background:#ffeaf1;color:#fb7299;cursor:pointer}",
        ".bhf-rs-cs{margin-bottom:4px}",
        ".bhf-rs-c{display:flex;gap:3px;margin-bottom:3px;align-items:center}",
        ".bhf-rs-c select,.bhf-rs-c input{padding:3px 4px;border:1px solid #dfe3e8;border-radius:5px;min-width:0;font-size:11px}",
        ".bhf-rs-cf{flex:0 0 40%}.bhf-rs-co{flex:0 0 30%}.bhf-rs-cv{flex:1}",
        ".bhf-rs-empty{color:#9499a0;padding:2px}",
        ".bhf-rs-msg{margin:6px 0;padding:4px 6px;border-radius:6px;min-height:16px}",
        ".bhf-rs-foot{display:flex;gap:6px}"
      ].join("");
      (document.head || document.documentElement).appendChild(css);
    } catch (e) {}
  }

  // ── 启动 ───────────────────────────────────────────────────────────
  function boot() {
    injectStyle();
    buildTopbar();
    // 抽屉挂到 body，按钮由顶栏切换显隐
    var drawer = BHF.UI.mountDrawer({ mount: document.body });
    // 规则集可视化编辑器（M5 补齐：单活跃切换 / 新增空白·预设 / 分组条件编辑 / 实时冲突提示）
    if (drawer) {
      var sec = document.createElement("div");
      sec.className = "bhf-rs-wrap";
      drawer.insertBefore(sec, drawer.firstChild);
      var editor = BHF.RuleSet.mountEditor({
        mount: sec,
        onChange: function () {                 // 规则生效后立刻重算读数 + 重扫角标
          refreshStat();
          var vp = document.querySelector("[data-bhf-views]");
          if (vp && vp.__render) vp.__render();
          if (window.__BHF_RESCAN__) window.__BHF_RESCAN__();
        }
      });
      if (drawer.__refresh && editor) {
        var _r = drawer.__refresh;
        drawer.__refresh = function () { _r(); };
      }
    }
    BHF.UI.mountViews({
      mount: document.body,
      getRecords: function () { return BHF.Storage.getAllDerived(); },
      onOpen: function (uri) { window.open(uri, "_blank", "noopener"); }
    });
    startOverlay();
    refreshStat();
    TB.setStatus("已启动 · 向下滚动可抓取更多");
  }
  if (document.body) boot();
  else document.addEventListener("DOMContentLoaded", boot, { once: true });

  // ⚠️ 关于「主动补请求」（见 SCHEMA.md 限制 1，已按方案决定）：
  //   本版**只被动旁听**页面自己发的请求，不主动翻页 GET。这是方案既定行为，
  //   不引入新功能、无需用户决策。代价：仅覆盖用户实际浏览过的分类（见限制 1/2/3），
  //   长期全量留存仍靠「定期导出 JSON → 回灌 Finder」。若日后确需主动补全，
  //   再在 fetch hook 之外加 fetchAllHistory()（默认关闭、单独评估风控）。
})();

