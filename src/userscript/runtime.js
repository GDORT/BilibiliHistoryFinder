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
