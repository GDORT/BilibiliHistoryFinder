// ==UserScript==
// @name         BHF Probe · 可行性探针（M-1）
// @namespace    https://github.com/GDORT/BilibiliHistoryFinder
// @version      0.2.0
// @description  【最小探针】验证能否稳定拦到 /x/web-interface/history/cursor 的完整响应。只读，不发请求、不回写。点右下角角标可一键导出诊断报告。
// @author       GDORT
// @match        https://www.bilibili.com/*
// @match        https://*.bilibili.com/*
// @run-at       document-start
// @grant        none
// ==/UserScript==
/*
 * ─────────────────────────────────────────────────────────────────────────────
 *  目的：回答 K5 ——「fetch hook 能否稳定装好、能否拿到完整响应」。
 *  这是整个油猴阶段**唯一可能让它需要重新评估**的卡点，所以在写任何存储 /
 *  引擎 / overlay 之前，先用一个最小脚本把它验掉。
 *
 *  ⚠️ 本脚本**只读**：不发请求、不回写、不改既有元素（只新增角标/面板）、
 *     用 `clone()` 读响应副本，原响应正常交回页面。
 *
 *  ⚠️ 为什么 **fetch 与 XHR 都 hook**：不知道历史页用哪个（axios = XHR）。
 *     只 hook fetch 而页面用 XHR → 一条都拦不到 → 会误判成"方案不可行"。
 *
 *  用法：装好后打开 https://www.bilibili.com/history（登录态），翻几页，
 *        然后**点右下角角标** —— 诊断报告会自动复制到剪贴板并弹出可复制面板，
 *        直接贴给开发者即可，不用翻控制台。
 * ─────────────────────────────────────────────────────────────────────────────
 */
(function () {
  'use strict';

  if (window.__BHF_PROBE__) return;   // 重复注入保护

  var TARGET = '/x/web-interface/history/cursor';
  var MAX_SEEN = 60;
  var MAX_TIMELINE = 40;

  // 错误码表 —— 直接沿用 src/collector.py 的 API_CODE_TABLE（不另发明一套）
  var CODE_TABLE = {
    '-101': ['致命', '账号未登录 / SESSDATA 已失效'],
    '-111': ['致命', 'csrf 校验失败'],
    '-400': ['致命', '请求参数错误'],
    '-403': ['限流', '访问权限不足（疑被风控拦截）'],
    '-412': ['限流', '请求被拦截（风控）'],
    '-509': ['限流', '请求过于频繁'],
    '-799': ['限流', '请求过于频繁'],
    '11000': ['致命', '登录态失效']
  };

  var state = {
    version: '0.2.0',
    startedAt: Date.now(),
    hook: { fetch: false, xhr: false },
    hits: [],
    seen: [],
    seenTargetRequests: 0,
    timeline: [],
    warn: []
  };
  window.__BHF_PROBE__ = state;

  // ── 基础工具 ────────────────────────────────────────────────────────────
  function tl(ev) {
    try {
      state.timeline.push({ t: Date.now() - state.startedAt, ev: ev });
      if (state.timeline.length > MAX_TIMELINE) state.timeline.shift();
    } catch (e) { /* 忽略 */ }
  }

  function urlOf(input) {
    if (typeof input === 'string') return input;
    if (input && typeof input.url === 'string') return input.url;
    if (input && typeof input.href === 'string') return input.href;
    return '';
  }

  function isTarget(u) { return typeof u === 'string' && u.indexOf(TARGET) !== -1; }

  function noteSeen(u) {
    try {
      if (isTarget(u)) {
        state.seenTargetRequests += 1;
        tl('目标请求 #' + state.seenTargetRequests + ' (' + String(u).slice(0, 80) + ')');
      }
      state.seen.push({ t: Date.now() - state.startedAt, u: String(u).slice(0, 160) });
      if (state.seen.length > MAX_SEEN) state.seen.shift();
    } catch (e) { /* 诊断信息，失败无所谓 */ }
  }

  function parseHit(url, status, text, via) {
    var hit = {
      t: Date.now() - state.startedAt,
      via: via,
      url: String(url).slice(0, 200),
      status: status,
      bytes: (text && text.length) || 0,
      code: null, listLen: null, cursor: null,
      topKeys: [], hasHistorySub: null, historyKeys: [],
      parseError: null
    };
    try {
      var d = JSON.parse(text);
      hit.code = (d && typeof d.code === 'number') ? d.code : null;
      var data = d && d.data;
      if (data) {
        var list = data.list || [];
        hit.listLen = list.length;
        hit.cursor = data.cursor || null;
        if (list.length) {
          hit.topKeys = Object.keys(list[0]).sort();
          var sub = list[0].history;
          hit.hasHistorySub = !!sub;
          if (sub && typeof sub === 'object') hit.historyKeys = Object.keys(sub).sort();
        }
      }
    } catch (e) {
      hit.parseError = String((e && e.message) || e).slice(0, 160);
    }
    return hit;
  }

  function pushHit(hit) {
    state.hits.push(hit);
    tl('捕获响应 via=' + hit.via + ' code=' + hit.code + ' list=' + hit.listLen);
    try {
      console.log('[BHF] 拦到 history/cursor  via=%s code=%s list=%s bytes=%d',
        hit.via, hit.code, hit.listLen, hit.bytes);
    } catch (e) { /* 忽略 */ }
    render();
  }

  // ── hook 1：window.fetch ────────────────────────────────────────────────
  try {
    var origFetch = window.fetch;
    if (typeof origFetch === 'function') {
      window.fetch = function (input) {
        var url = urlOf(input);
        noteSeen(url);
        var p = origFetch.apply(this, arguments);
        if (isTarget(url) && p && typeof p.then === 'function') {
          p.then(function (res) {
            try {
              if (res && typeof res.clone === 'function') {
                // ⚠️ 必须 clone：直接读 body 会把原响应消耗掉，页面会崩
                res.clone().text().then(function (txt) {
                  pushHit(parseHit(url, res.status, txt, 'fetch'));
                }).catch(function (e) {
                  state.warn.push('fetch 读响应失败: ' + e);
                });
              }
            } catch (e) { state.warn.push('fetch clone 失败: ' + e); }
            return res;
          }).catch(function () { /* 原请求失败：交给页面 */ });
        }
        return p;
      };
      state.hook.fetch = true;
    }
  } catch (e) {
    state.warn.push('fetch hook 安装失败: ' + e);
  }

  // ── hook 2：XMLHttpRequest ──────────────────────────────────────────────
  try {
    var OrigXHR = window.XMLHttpRequest;
    if (OrigXHR && OrigXHR.prototype) {
      var origOpen = OrigXHR.prototype.open;
      var origSend = OrigXHR.prototype.send;
      OrigXHR.prototype.open = function () {
        this.__bhf_url = String(arguments[1] || '');
        return origOpen.apply(this, arguments);
      };
      OrigXHR.prototype.send = function () {
        var self = this;
        var u = this.__bhf_url || '';
        noteSeen(u);
        if (isTarget(u)) {
          this.addEventListener('readystatechange', function () {
            if (self.readyState === 4) {
              try {
                pushHit(parseHit(u, self.status, self.responseText || '', 'xhr'));
              } catch (e) { state.warn.push('xhr 解析失败: ' + e); }
            }
          });
        }
        return origSend.apply(this, arguments);
      };
      state.hook.xhr = true;
    }
  } catch (e) {
    state.warn.push('XHR hook 安装失败: ' + e);
  }

  // ══════════════════════════════════════════════════════════════════════
  //  自动诊断 —— **纯函数**：只看入参，不改任何东西
  //  ⇒ 因此能被各种"人造故障"喂一遍，验证诊断结论是否正确
  //    （见 userscript/tools/verify_hook.py 的「诊断场景」段）
  // ══════════════════════════════════════════════════════════════════════
  function diagnose(s) {
    var steps = [];
    var hits = s.hits || [];

    steps.push({
      id: 'hook', name: '1. hook 装载',
      ok: !!(s.hook && (s.hook.fetch || s.hook.xhr)),
      detail: 'fetch=' + !!(s.hook && s.hook.fetch) + ' xhr=' + !!(s.hook && s.hook.xhr)
    });
    steps.push({
      id: 'seen', name: '2. 见到目标请求',
      ok: (s.seenTargetRequests || 0) > 0,
      detail: '次数=' + (s.seenTargetRequests || 0)
    });
    steps.push({
      id: 'capture', name: '3. 捕获到响应',
      ok: hits.length > 0,
      detail: '条数=' + hits.length
    });
    var bad = null;
    for (var i = 0; i < hits.length; i++) {
      if (hits[i].parseError) { bad = hits[i].parseError; break; }
    }
    steps.push({
      id: 'parse', name: '4. JSON 解析',
      ok: hits.length > 0 && !bad,
      detail: bad ? ('错误: ' + bad) : '正常'
    });
    var codes = hits.map(function (h) { return h.code; });
    var nonZero = codes.filter(function (c) { return c !== 0; });
    steps.push({
      id: 'code', name: '5. 业务码 code=0',
      ok: hits.length > 0 && nonZero.length === 0,
      detail: 'code=' + JSON.stringify(codes)
    });

    var failed = null;
    for (var k = 0; k < steps.length; k++) {
      if (!steps[k].ok) { failed = steps[k]; break; }
    }

    var v = {
      level: 'ok', title: '', reason: '', action: '',
      steps: steps, failedStep: failed ? failed.id : null
    };

    if (!failed) {
      v.title = 'K5 通过：稳定拦到 ' + hits.length + ' 条';
      v.reason = 'hook 装载、请求捕获、解析、业务码 全部正常';
      v.action = '把报告贴给开发者，即可进入 M0（IndexedDB 双存储）';
      return v;
    }

    v.level = 'fail';
    if (failed.id === 'hook') {
      v.title = 'hook 完全没装上';
      v.reason = '@run-at document-start 或 @grant none 未生效';
      v.action = '检查油猴脚本头部元数据是否被改动；确认脚本已启用且匹配当前域名';
    } else if (failed.id === 'seen') {
      v.title = '未见到目标请求';
      v.reason = 'hook 装好了，但页面没发 /x/web-interface/history/cursor —— '
        + '可能接口已变更，或历史页还没真正加载数据';
      v.action = '确认在 bilibili.com/history 且已登录；往下翻几页触发加载；'
        + '报告里的 seenSummary 会列出实际出现过哪些请求，据此判断新接口路径';
    } else if (failed.id === 'capture') {
      v.title = '见到请求但没读到响应';
      v.reason = '目标请求出现了 ' + (s.seenTargetRequests || 0) + ' 次，但一条响应都没捕获';
      v.action = '多半是响应体读取失败（流式消耗 / 重定向 / 跨域）；看报告里的 warn 字段';
    } else if (failed.id === 'parse') {
      v.title = '响应不是合法 JSON';
      v.reason = String(bad);
      v.action = '可能拿到了 HTML（未登录跳转页）或空响应；看 hits[0].url 与 status';
    } else if (failed.id === 'code') {
      var c = nonZero[0];
      var info = CODE_TABLE[String(c)];
      v.title = '接口返回业务错误 code=' + c;
      v.reason = info ? (info[0] + '：' + info[1]) : ('未收录的错误码 ' + c);
      v.action = (info && (String(c) === '-101' || String(c) === '11000'))
        ? '登录态问题：请先在浏览器登录 B站，再刷新历史页'
        : '看报告里 hits 的详情';
    }
    return v;
  }
  state.diagnose = function () { return diagnose(state); };
  // 暴露**纯函数本身**（而非只暴露"诊断当前 state"的闭包）—— 这样测试可以喂
  // 各种人造状态进来，验证诊断结论是否正确。**诊断逻辑自己必须有护栏**，
  // 否则用户看到的"失败原因"可能是错的，那比没有诊断更糟。
  state.diagnoseWith = diagnose;

  // ── 报告：一步到位，直接贴给开发者 ──────────────────────────────────────
  function summarizeSeen() {
    var m = {};
    (state.seen || []).forEach(function (x) {
      var p = String(x.u).split('?')[0];
      m[p] = (m[p] || 0) + 1;
    });
    return Object.keys(m).map(function (p) {
      return { path: p.slice(0, 120), n: m[p], isHistory: p.indexOf('history') !== -1 };
    }).sort(function (a, b) { return b.n - a.n; }).slice(0, 25);
  }

  function envInfo() {
    var e = { url: '', onHistoryPage: false, loggedIn: null, ua: '', ts: new Date().toISOString() };
    try {
      e.url = location.href.slice(0, 160);
      e.onHistoryPage = location.pathname.indexOf('/history') !== -1;
      // DedeUserID 不是 HttpOnly，前端可读 —— 用它判断登录态最省事
      var ck = document.cookie || '';
      e.loggedIn = ck.indexOf('DedeUserID') !== -1 || ck.indexOf('buvid3') !== -1;
      e.ua = (navigator.userAgent || '').slice(0, 120);
    } catch (err) { /* 忽略 */ }
    return e;
  }

  function report() {
    var d = diagnose(state);
    return {
      meta: { tool: 'BHF Probe', version: state.version, generatedAt: new Date().toISOString() },
      env: envInfo(),
      verdict: {
        level: d.level, title: d.title, reason: d.reason,
        action: d.action, failedStep: d.failedStep
      },
      steps: d.steps,
      hook: state.hook,
      counts: {
        hits: state.hits.length,
        seenTargetRequests: state.seenTargetRequests,
        seenTotal: state.seen.length
      },
      seenSummary: summarizeSeen(),
      hits: state.hits.slice(0, 5),
      timeline: state.timeline.slice(0, 30),
      warn: state.warn
    };
  }
  state.report = report;

  function reportText() {
    var r = report();
    var L = [];
    L.push('# BHF 探针诊断报告');
    L.push('');
    L.push('- 生成时间：' + r.meta.generatedAt + '（探针 v' + r.meta.version + '）');
    L.push('- 页面：' + r.env.url);
    L.push('- 在历史页：' + r.env.onHistoryPage + '　已登录：' + r.env.loggedIn);
    L.push('');
    L.push('## 结论');
    L.push('');
    L.push((r.verdict.level === 'ok' ? '✅ ' : '❌ ') + r.verdict.title);
    L.push('');
    L.push('- 原因：' + r.verdict.reason);
    L.push('- 建议：' + r.verdict.action);
    L.push('');
    L.push('## 分步结果');
    L.push('');
    L.push('| 步骤 | 结果 | 详情 |');
    L.push('| --- | --- | --- |');
    r.steps.forEach(function (s) {
      L.push('| ' + s.name + ' | ' + (s.ok ? '通过' : '失败')
        + ' | ' + String(s.detail).replace(/\|/g, '/') + ' |');
    });
    L.push('');
    L.push('## 计数');
    L.push('');
    L.push('```json');
    L.push(JSON.stringify(r.counts));
    L.push('```');
    L.push('');
    if (r.hits.length) {
      L.push('## 抓到的响应（最多 5 条）');
      L.push('');
      L.push('```json');
      L.push(JSON.stringify(r.hits, null, 1).slice(0, 4000));
      L.push('```');
      L.push('');
    }
    L.push('## 出现过哪些请求（去重 top25，`isHistory` 为 true 的尤其重要）');
    L.push('');
    L.push('```json');
    L.push(JSON.stringify(r.seenSummary, null, 1).slice(0, 2500));
    L.push('```');
    L.push('');
    L.push('## 时间线（毫秒，相对脚本启动）');
    L.push('');
    L.push('```');
    L.push(r.timeline.map(function (x) { return x.t + 'ms  ' + x.ev; }).join('\n') || '(空)');
    L.push('```');
    if (r.warn.length) {
      L.push('');
      L.push('## 告警');
      L.push('');
      L.push(r.warn.join('\n'));
    }
    return L.join('\n');
  }
  state.reportText = reportText;

  // ── 角标 + 一键导出 ─────────────────────────────────────────────────────
  var badge = null;
  function render() {
    try {
      if (!badge) {
        if (!document.body) return;
        badge = document.createElement('div');
        badge.id = 'bhf-probe-badge';
        badge.setAttribute('style', [
          'position:fixed', 'right:12px', 'bottom:12px', 'z-index:2147483647',
          'padding:8px 12px', 'border-radius:8px', 'cursor:pointer',
          'font:bold 13px/1.5 -apple-system,"Microsoft YaHei",sans-serif',
          'background:#fb7299', 'color:#fff', 'box-shadow:0 3px 10px rgba(0,0,0,.3)'
        ].join(';'));
        badge.title = '点击生成诊断报告并复制';
        badge.addEventListener('click', exportReport);
        document.body.appendChild(badge);
      }
      var v = diagnose(state);
      badge.textContent = v.level === 'ok'
        ? ('✅ BHF 抓到 ' + state.hits.length + ' 条 · 点我导出')
        : ('⚠️ BHF 未通过 · 点我导出');
      badge.style.background = v.level === 'ok' ? '#2fa84f' : '#e5534b';
    } catch (e) { /* 角标只是辅助 */ }
  }

  function exportReport() {
    var txt = reportText();
    var r = report();
    try { console.log('[BHF] 诊断结论：' + r.verdict.title); } catch (e) { /* 忽略 */ }
    try { console.log('[BHF] 完整报告对象：', r); } catch (e) { /* 忽略 */ }
    try { console.log(txt); } catch (e) { /* 忽略 */ }

    var triedCopy = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(function () {
          flash('已复制到剪贴板', '#2fa84f');
        }, function () {
          flash('复制失败，请手动选中复制', '#e5534b');
        });
        triedCopy = true;
      }
    } catch (e) { /* 忽略 */ }

    // 无论剪贴板成不成功，都弹一个可手动复制的面板（兜底）
    showPanel(txt, triedCopy);
  }
  state.exportReport = exportReport;

  var panel = null;
  function showPanel(txt, triedCopy) {
    try {
      if (panel) { panel.remove(); panel = null; }
      panel = document.createElement('div');
      panel.id = 'bhf-probe-panel';
      panel.setAttribute('style', [
        'position:fixed', 'left:50%', 'top:50%', 'transform:translate(-50%,-50%)',
        'z-index:2147483647', 'width:min(760px,92vw)', 'max-height:78vh',
        'background:#fff', 'color:#18191c', 'border-radius:10px', 'padding:14px',
        'box-shadow:0 8px 32px rgba(0,0,0,.4)',
        'font:12px/1.6 -apple-system,"Microsoft YaHei",sans-serif'
      ].join(';'));

      var head = document.createElement('div');
      head.textContent = triedCopy
        ? 'BHF 诊断报告（已尝试复制到剪贴板；若没成功，在这里全选复制）'
        : 'BHF 诊断报告（请全选复制后贴给开发者）';
      head.style.cssText = 'font-weight:bold;margin-bottom:8px';

      var ta = document.createElement('textarea');
      ta.value = txt;
      ta.setAttribute('readonly', 'readonly');
      ta.style.cssText = 'width:100%;height:54vh;box-sizing:border-box;'
        + 'font:11px/1.5 Consolas,monospace;border:1px solid #ccd;padding:8px;'
        + 'border-radius:6px;resize:none';

      var btn = document.createElement('button');
      btn.textContent = '关闭';
      btn.style.cssText = 'margin-top:8px;padding:5px 14px;cursor:pointer';
      btn.addEventListener('click', function () {
        if (panel) { panel.remove(); panel = null; }
      });

      panel.appendChild(head);
      panel.appendChild(ta);
      panel.appendChild(btn);
      document.body.appendChild(panel);
      ta.focus();
      ta.select();
    } catch (e) { /* 面板失败不影响复制 */ }
  }

  function flash(msg, color) {
    try {
      if (!badge) return;
      badge.textContent = msg;
      badge.style.background = color;
      setTimeout(render, 2200);
    } catch (e) { /* 忽略 */ }
  }

  if (document.body) render();
  else document.addEventListener('DOMContentLoaded', render, { once: true });

  tl('探针已启动 fetch=' + state.hook.fetch + ' xhr=' + state.hook.xhr);
  try {
    console.log('[BHF Probe] 已启动（v' + state.version + '）  fetch=%s  xhr=%s'
      + '  —— 点右下角角标可导出诊断报告',
      state.hook.fetch, state.hook.xhr);
  } catch (e) { /* 忽略 */ }
})();
