// ============================================================================
// BHF 规则诊断 —— 回答「为什么只有『已看完』有角标」
//
// 前置：已装 dist/bili_history.user.js（0.2.0）并已打开过历史页（库里已有数据）。
// 用法：B站历史页（或任意 bilibili 页面）F12 → Console → 粘贴本脚本 → 回车。
//       会弹窗显示结果，Ctrl+C 复制后发给开发者。
//
// 只读本地数据与 DOM，不向 B站发任何请求。
// ============================================================================
(async function () {
  function out(s) { console.log(s); }
  if (!window.BHF) { out("[BHF-DIAG] 没检测到 BHF —— 脚本可能没装或没生效"); return; }
  var E = BHF.Engine, S = BHF.Storage, O = BHF.Overlay;
  var now = Math.floor(Date.now() / 1000);
  var L = [];

  var der = await S.getAllDerived();
  L.push("库内记录总数: " + der.length);
  if (!der.length) { out(L.join("\n")); window.prompt("复制诊断结果：", L.join("\n")); return; }

  // ---- 1. 字段健康度：duration 为 0 会让所有时长/进度规则失效 ----
  var badDur = 0, badProg = 0, fin = 0, biz = {};
  der.forEach(function (d) {
    if (!d.duration || d.duration <= 0) badDur++;
    if (d.progress === null || d.progress === undefined) badProg++;
    if (d.is_finish || d.progress === -1) fin++;
    biz[d.business || "(空)"] = (biz[d.business || "(空)"] || 0) + 1;
  });
  L.push("");
  L.push("== 字段健康 ==");
  L.push("  is_finish/看完      : " + fin);
  L.push("  duration<=0 或空    : " + badDur + (badDur > der.length * 0.5 ? "   ← ⚠️ 超过一半，规则会被 #41 守卫全部挡掉" : ""));
  L.push("  progress 为空       : " + badProg);
  L.push("  business 分布       : " + JSON.stringify(biz));

  // ---- 2. 默认规则分类结果 ----
  var rule = E.get_active_rule(E.default_rules());
  var cls = E.classify(der, rule, now);
  L.push("");
  L.push("== 默认规则分类 ==");
  L.push("  已看完(finished)    : " + cls.finished + "   ← 这个会显示角标");
  L.push("  已跳过(auto_skip)   : " + cls.auto_skip + "   ← 会显示角标");
  L.push("  已搁置(stale)       : " + cls.stale + "   ← 会显示角标");
  L.push("  needs(不显示任何角标): " + cls.needs);

  // ---- 3. 逐条规则命中数（含被 duration 挡掉的）----
  L.push("");
  L.push("== 逐条规则命中数 ==");
  (rule.groups || []).forEach(function (g) {
    var n = 0, blocked = 0;
    der.forEach(function (d) {
      if (d.progress === -1) return;
      var durOK = d.duration && d.duration > 0;
      var usesDur = JSON.stringify(g.conditions || []).match(/duration|progress_pct|progress_sec/) ? true : false;
      if (!durOK && usesDur) { blocked++; return; }
      if (E.eval_group(d, g, now, null)) n++;
    });
    L.push("  " + (g.label || "?") + " : 命中 " + n + (blocked ? " / 另有 " + blocked + " 条因缺 duration 被挡" : ""));
  });

  // ---- 4. DOM 侧：卡片数 vs 成功反查数 ----
  var cards = document.querySelectorAll(".bili-video-card");
  var records = {}; der.forEach(function (d) { records[d.kid] = d; });
  var resolver = O.buildResolver(records);
  var hit = 0;
  for (var i = 0; i < cards.length; i++) { if (resolver(cards[i])) hit++; }
  L.push("");
  L.push("== DOM 匹配 ==");
  L.push("  .bili-video-card 卡片数: " + cards.length);
  L.push("  成功反查到 kid 的卡片 : " + hit + (cards.length && hit < cards.length * 0.5 ? "   ← ⚠️ 匹配率低，选择器可能不对" : ""));

  var txt = L.join("\n");
  out("=====BHF-DIAG-BEGIN=====\n" + txt + "\n=====BHF-DIAG-END=====");
  try { window.prompt("复制以下诊断结果发给开发者：", txt); } catch (e) {}
})();