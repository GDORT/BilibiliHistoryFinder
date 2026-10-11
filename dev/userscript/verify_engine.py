# -*- coding: utf-8 -*-
"""M1/M2 验证：engine.py(JS 移植) 对拍 ＋ 引擎一致性。

两层验证：
  A. 引擎单测（移植忠实度）：在 headless Chromium 里跑 JS 版 BHF.Engine，
     覆盖 _derived / eval_condition（含 #41 守卫）/ eval_group / evaluate_rule /
     classify（五分类不变式）/ validate_rule / sort_value / apply_sort（#42 分段）/
     evaluate_filter —— 与 dev/test_engine.py 同口径。
  B. 对拍（M2 硬要求）：同一批记录分别喂 Python engine 与 JS BHF.Engine，
     classify 的计数 ＋ 样本动作/标签 必须逐字节一致。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", "..", "src", "userscript", "core.js"))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC_PY = os.path.join(ROOT, "src")
if SRC_PY not in sys.path:
    sys.path.insert(0, SRC_PY)
import engine  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception as e:  # noqa
    print("  x 需要 playwright：", e)
    sys.exit(2)


def launch_browser():
    env = os.environ
    browsers = env.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(ROOT, "dev", "e2e", "browsers")
    p = sync_playwright().start()
    browser = p.chromium.launch()
    return p, browser


# ---------------- A. 引擎单测（注入到页面跑 JS） ----------------
ENGINE_TEST_JS = r"""
(function(){
  const E = window.BHF.Engine;
  const out = [];
  const ok = (c, tag, detail) => out.push({tag, ok: !!c, detail: detail||""});
  const eq = (g, w, tag) => ok(JSON.stringify(g)===JSON.stringify(w), tag, "got="+JSON.stringify(g)+" want="+JSON.stringify(w));
  const NOW = 1700000000, DAY = 86400;
  const rec = (o) => Object.assign({kid:"k1",bvid:"BV1",title:"t",business:"archive",view_at:NOW-10*DAY,progress:0,duration:0,author_name:"a"}, o||{});

  // _derived
  let d = E._derived(rec({progress:-1,duration:100}), NOW);
  eq(d.progress_pct, 1.0, "derived progress=-1 → pct=1.0");
  eq(d.progress_sec, 100, "derived progress=-1 → sec=100");
  d = E._derived(rec({progress:30,duration:120}), NOW);
  eq(Math.round(d.progress_pct*10000)/10000, 0.25, "derived 30/120 → 0.25");
  [0,null].forEach(x => { let dd=E._derived(rec({progress:0,duration:x}),NOW); ok(dd.progress_pct===null, "derived duration="+JSON.stringify(x)+" → pct=null"); });

  // eval_condition：算子全集
  const r = rec({progress:30,duration:120,business:"archive",view_at:NOW-100*DAY,title:"Rust 入门",author_name:"alice",tag_name:"编程",remark:"互动补充：xx",main_category:"科技",is_fav:1,videos:3,dt:"web"});
  const ev = (f,o,v)=>E.eval_condition(r,{field:f,op:o,value:v},NOW);
  ok(ev("progress_pct",">=",0.2)===true, "pct>=0.2");
  ok(ev("progress_pct","<",0.2)===false, "pct<0.2 false");
  ok(ev("progress","==",30)===true, "== 30");
  ok(ev("business","in",["archive","live"])===true, "in 列表");
  ok(ev("business","not_in",["archive","live"])===false, "not_in 列表");
  ok(ev("progress","between","10-40")===true, "between 10-40");
  ok(ev("progress","between","50-90")===false, "between 50-90");
  ok(ev("progress","not_between","10-40")===false, "not_between");
  ok(ev("progress","between","坏格式")===false, "between 坏格式→false");
  ok(ev("view_at_age_days",">",90)===true, "view_at_age_days>90");
  ok(ev("title","contains","入门")===true, "contains");
  ok(ev("title","not_contains","入门")===false, "not_contains");
  ok(ev("title","regex",{source:"^Rust"},NOW)===true || ev("title","regex","^Rust")===true, "regex");
  ok(ev("is_fav","exists",null)===true, "exists 有值");
  ok(ev("remark","empty",null)===false, "empty 有值");
  ok(ev("view_at","relative_after","200d")===true, "relative_after 200d");
  ok(ev("view_at","relative_after","10d")===false, "relative_after 10d");
  ok(ev("view_at","relative_before","10d")===true, "relative_before 10d");
  ok(ev("view_at","relative_after",null)===false, "view_at 缺失→false");
  ok(ev("nonexistent_field",">",1)===false, "不存在字段→false（不崩）");
  ok(ev("tag_name","contains","x")===false, "空字段 contains→false");

  // #41 守卫
  ok(ev("duration","<",60)===false, "#41 缺 duration 不参与时长类");
  ok(E.eval_condition(rec({duration:0}),{field:"duration",op:">",value:60},NOW)===false, "#41 缺 duration 反向也算 false（已知取舍）");
  ok(ev("duration","exists",null)===true, "#41 守卫不拦 exists（duration=120 算有值）");
  ok(E.eval_condition(rec({duration:null}),{field:"duration",op:"empty",value:null},NOW)===true, "#41 守卫不拦 empty（duration=null 算空）");
  ok(E.eval_condition(rec({duration:1}),{field:"duration",op:"<",value:60},NOW)===true, "#41 duration=1 照常参与");
  ok(E.eval_condition(rec({duration:30}),{field:"duration",op:"<",value:60},NOW)===true, "#41 真实短视频仍命中");
  // 守卫只作用 duration 字段
  ok(E.eval_condition(rec({progress:0,duration:null}),{field:"progress",op:"<",value:15},NOW)===true, "#41 守卫不越权（progress 缺别的照常）");

  // eval_group / evaluate_rule
  const cond=(f,o,v)=>({field:f,op:o,value:v});
  ok(E.eval_group(r,{conditions:[cond("progress_pct","<",0.1),cond("progress_pct",">=",0.9)]},NOW)===false, "组内 AND 只满足一个→false");
  const any_rule={id:"a",active:true,match:"any",groups:[{label:"A",action:"auto_skip",conditions:[cond("progress_pct","<",0.1)]},{label:"B",action:"auto_skip",conditions:[cond("progress_pct",">=",0.9)]}]};
  let hit=E.evaluate_rule(rec({progress:95,duration:100}),any_rule,NOW);
  ok(hit && hit[0]==="auto_skip" && hit[1]==="B", "any 命中第一个命中组(B)");
  ok(E.evaluate_rule(rec({progress:-1,duration:100}),any_rule,NOW)===null, "evaluate_rule 已看完→null");

  // classify 五分类
  const rule = E.default_rules().rules[0];
  const cls = (o)=>{ const out=E.classify([rec(o)],rule,NOW); for(const b of ["auto_skip","stale"]){ if(out[b]){ return out.samples[b][0].action+":"+ (out.samples[b][0].label); } } return out.auto_skip||out.stale ? "" : (out.needs? "needs":"?"); };
  ok(cls({progress:98,duration:100})==="auto_skip:接近看完", "接近看完");
  ok(cls({progress:10,duration:45})==="auto_skip:短视频碎片", "短视频碎片");
  ok(cls({business:"live",progress:300,duration:900})==="auto_skip:直播回放", "直播回放");
  ok(cls({business:"live",progress:0,duration:0})==="auto_skip:直播回放", "#41 修好：缺时长直播→直播回放（不被碎片抢）");
  ok(cls({view_at:NOW-200*DAY,progress:50,duration:1000})==="stale:陈旧-久未观看", "陈旧");
  ok(cls({progress:50,duration:300,view_at:NOW-DAY})==="needs", "普通→needs");
  ok(cls({progress:0,duration:0})==="needs", "#41 缺 duration 不再误判碎片");
  ok(cls({progress:0,duration:null})==="needs", "duration=null 同样不参与");
  ok(cls({progress:5,duration:30})==="auto_skip:短视频碎片", "真实30s仍命中（本意未损）");
  // 不变量
  const recs=[rec({progress:-1,duration:100}),rec({progress:98,duration:100}),rec({view_at:NOW-200*DAY,progress:50,duration:1000}),rec({progress:50,duration:300})];
  const o=E.classify(recs,rule,NOW);
  ok(o.finished+o.auto_skip+o.stale+o.needs===o.total, "不变量 total 拆分");
  ok(o.finished===1 && o.stale===1, "finished=1 stale=1");

  // validate_rule
  const bad={id:"x",active:true,match:"all",groups:[{label:"G",action:"auto_skip",conditions:[{field:"progress_pct",op:">=",value:0.9},{field:"progress_pct",op:"<=",value:0.1}]}]};
  ok(E.validate_rule(bad).hard.length>0, "validate_rule 同组 >=0.9 & <=0.1 → hard");
  ok(E.validate_rule(rule).hard.length===0, "默认规则无 hard");

  // 排序 #42
  const mk=()=>[rec({kid:"a",view_at:NOW-3*DAY,duration:300,progress:30,title:"BBB"}),rec({kid:"b",view_at:NOW-1*DAY,duration:100,progress:90,title:"AAA"}),rec({kid:"c",view_at:null,duration:null,progress:null,title:"CCC"})];
  let rs=mk(); E.apply_sort(rs,[{field:"view_at",dir:"desc"}],NOW);
  ok(rs.map(x=>x.kid).join(",")==="b,a,c", "#42 降序缺值在末尾");
  ok(E.apply_sort(mk(),[{field:"view_at",dir:"desc"}],NOW)===undefined, "apply_sort 返回 undefined（原地）");
  let rs2=mk(); E.apply_sort(rs2,[{field:"title",dir:"asc"}],NOW);
  ok(rs2[rs2.length-1].kid==="c", "#42 升序缺值在末尾");
  let rs3=mk(); E.apply_sort(rs3,[{field:"不存在",dir:"asc"}],NOW);
  ok(rs3.length===3, "未知排序字段→不崩不丢");

  // evaluate_filter
  ok(E.evaluate_filter(r,{field:"progress_pct",op:">=",value:0.2},NOW)===true, "filter 通过");
  ok(E.evaluate_filter(r,{},NOW)===true, "空 filter→true");
  ok(E._parse_duration("7d")===7*DAY, "_parse_duration 7d");
  ok(E._parse_duration("24h")===DAY, "_parse_duration 24h");
  ok(E._parse_duration(null)===0, "_parse_duration null→0");

  return out;
})();
"""


def run_engine_unit(browser):
    page = browser.new_page()
    page.add_script_tag(path=SRC)
    res = page.evaluate(ENGINE_TEST_JS)
    page.close()
    fails = [r for r in res if not r["ok"]]
    return res, fails


# ---------------- B. 对拍（M2） ----------------
def build_batch():
    NOW = 1700000000
    DAY = 86400
    def rec(o):
        base = dict(kid="k1", bvid="BV1", title="t", business="archive",
                    view_at=NOW - 10 * DAY, progress=0, duration=0, author_name="a")
        base.update(o)
        return base
    batch = [
        rec(dict(kid="f1", progress=-1, duration=100)),                         # finished
        rec(dict(kid="n1", progress=98, duration=100)),                          # 接近看完
        rec(dict(kid="s1", progress=10, duration=45)),                           # 短视频碎片
        rec(dict(kid="l1", business="live", progress=300, duration=900)),        # 直播回放
        rec(dict(kid="l2", business="live", progress=0, duration=0)),            # 缺时长直播→直播回放(#41)
        rec(dict(kid="st1", view_at=NOW - 200 * DAY, progress=50, duration=1000)),# 陈旧
        rec(dict(kid="nd1", progress=50, duration=300, view_at=NOW - DAY)),      # needs
        rec(dict(kid="d0", progress=0, duration=0)),                             # 缺duration→needs(#41)
        rec(dict(kid="sv1", progress=5, duration=30)),                           # 真实短视频→碎片
    ]
    return batch, NOW


def py_classify(batch, now):
    rule = engine.default_rules()["rules"][0]
    out = engine.classify(batch, rule, now=now)
    # 归一化样本：只保留 (kid, action, label) 便于比较
    s = {}
    for b in ("auto_skip", "stale", "needs"):
        s[b] = sorted([(x["kid"], x.get("action", ""), x.get("label", "")) for x in out["samples"][b]])
    return {"total": out["total"], "finished": out["finished"], "auto_skip": out["auto_skip"],
            "stale": out["stale"], "needs": out["needs"],
            "samples": json.loads(json.dumps(s))}


def js_classify(browser, batch, now):
    page = browser.new_page()
    page.add_script_tag(path=SRC)
    res = page.evaluate(
        """(function(data){
             const batch = data.batch, now = data.now;
             const rule = BHF.Engine.default_rules().rules[0];
             const out = BHF.Engine.classify(batch, rule, now);
             const s = {};
             ['auto_skip','stale','needs'].forEach(function(b){
               s[b] = out.samples[b].map(function(x){return [x.kid, x.action||'', x.label||''];}).sort();
             });
             return {total:out.total, finished:out.finished, auto_skip:out.auto_skip,
                     stale:out.stale, needs:out.needs, samples:s};
           })""",
        {"batch": batch, "now": now})
    page.close()
    return res


def main():
    t0 = time.time()
    if not os.path.exists(SRC):
        print("  x 找不到 core.js：", SRC); sys.exit(1)
    engine_fail = 0
    unit_len = 0
    parity_bad = False
    p, browser = launch_browser()
    try:
        print("\n== A. 引擎单测（JS 移植忠实度） ==")
        unit, fails = run_engine_unit(browser)
        unit_len = len(unit)
        engine_fail = len(fails)
        for r in unit:
            if not r["ok"]:
                print("  x FAIL %s  | %s" % (r["tag"], r["detail"]))
        print("  引擎单测：%d 通过 / %d 失败" % (unit_len - engine_fail, engine_fail))

        print("\n== B. 对拍（Python vs JS，M2） ==")
        batch, now = build_batch()
        py = py_classify(batch, now)
        js = js_classify(browser, batch, now)
        parity_fail = []
        for k in ("total", "finished", "auto_skip", "stale", "needs"):
            if py[k] != js[k]:
                parity_fail.append("计数 %s: py=%s js=%s" % (k, py[k], js[k]))
        if json.dumps(py["samples"], sort_keys=True) != json.dumps(js["samples"], sort_keys=True):
            parity_fail.append("样本不一致:\n   py=%s\n   js=%s" % (py["samples"], js["samples"]))
        for line in parity_fail:
            print("  x FAIL", line)
        if not parity_fail:
            print("  对拍：计数 + 样本 100%% 一致（%d 条记录）" % len(batch))
        parity_bad = bool(parity_fail)
    finally:
        browser.close()
        p.stop()

    print("\n" + "=" * 60)
    if engine_fail == 0 and not parity_bad:
        print("  结果：全部通过（引擎单测 %d 项 ＋ 对拍）" % unit_len)
        print("  耗时 %.1fs" % (time.time() - t0))
        return 0
    print("  结果：引擎单测 %d 失败 ＋ 对拍 %s" % (engine_fail, "失败" if parity_bad else "通过"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
