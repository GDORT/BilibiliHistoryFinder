# 测试过程日志（`dev/run_all.py` 自动追加）

> **这是本仓「跑过没有」的独立时间线**（2026-10-06 建立）——此前执行记录只散落在
> `dev/README.md` §6.5 与各 `log/` 审查稿里，没有「测试轮次」这条独立线索。

**看这一份就能回答**：「最近一次全绿是什么时候、在哪种环境跑的、各项断言多少、文档声明对不对」。

---

## 2026-10-06 18:55:10  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 95｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 96｜14.5s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 —｜11.4s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜12.9s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 18:57:12  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 95｜0.4s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.8s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 96｜15.3s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.9s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.6s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.3s
- 声明校验：**5 处不符**｜零污染：通过
- **总判定：❌ 有失败**

## 2026-10-06 18:59:20  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 n/a｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 96｜14.7s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.6s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.1s
- 声明校验：通过｜零污染：通过
- **总判定：❌ 有失败**

## 2026-10-06 19:00:32  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 n/a｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 96｜14.6s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.4s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.1s
- 声明校验：通过｜零污染：通过
- **总判定：❌ 有失败**

## 2026-10-06 19:01:53  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 95｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 96｜13.9s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.7s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.3s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.1s
- 声明校验：**1 处不符**｜零污染：通过
- **总判定：❌ 有失败**

## 2026-10-06 19:03:55  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 95｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 96｜14.6s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜4.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.3s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜12.8s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 19:09:35  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 95｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 96｜14.6s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.2s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.5s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜13.0s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 19:11:49  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 95｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 96｜13.9s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.3s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜13.0s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 19:47:08  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.4s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜16.6s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜12.0s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.3s
- 声明校验：**2 处不符**｜零污染：通过
- **总判定：❌ 有失败**

## 2026-10-06 19:52:44  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.4s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.3s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.7s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.9s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.9s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.2s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 19:58:57  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜16.5s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜10.1s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.8s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜13.9s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 21:15:04  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.4s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.8s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜10.1s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜1.1s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.5s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.6s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.6s
- `test_launch_analyzer.py` — Analyzer 自动启动器（判定逻辑，不真启动）｜断言 30｜21.0s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 21:19:22  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜17.0s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜11.0s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.9s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.4s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.9s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.3s
- `test_launch_analyzer.py` — Analyzer 自动启动器（判定逻辑，不真启动）｜断言 30｜20.9s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 22:00:04  runner
- 环境：当前环境（本机无代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.2s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.2s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜12.3s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.4s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.4s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.6s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.1s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜11.5s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 22:15:50  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.9s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.7s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.9s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜12.4s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.5s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 22:38:24  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.7s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.6s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.7s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.3s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 22:48:05  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.6s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.9s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.6s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.3s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-06 22:50:26  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.8s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.6s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.7s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.9s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.8s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.3s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-07 23:02:05  runner
- 环境：带代理／无代理（本机无代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.1s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.2s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜12.3s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.2s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.3s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.0s
- `regression_restart.py` — 重启链路｜断言 n/a｜9.3s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜12.7s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 00:03:17  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.5s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜10.1s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜1.1s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.4s
- `regression_restart.py` — 重启链路｜断言 n/a｜12.3s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜18.9s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 01:11:59  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.7s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.6s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.6s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.4s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜18.8s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 01:13:27  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.8s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.0s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.2s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.5s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜18.7s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 01:15:47  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.1s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.8s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.4s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.5s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 01:17:01  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.1s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.0s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.2s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.4s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜18.7s
- 声明校验：通过｜零污染：通过
- **总判定：❌ 有失败**

## 2026-10-08 01:18:16  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.1s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.9s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜4.7s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.2s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜18.7s
- 声明校验：通过｜零污染：通过
- **总判定：❌ 有失败**

## 2026-10-08 01:19:24  runner
- 环境：当前环境（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.0s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.5s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.7s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.3s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜19.4s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 01:22:20  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜13.9s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.3s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜4.7s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.3s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.6s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 02:15:27  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.2s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜13.7s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.1s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.2s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.2s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.3s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 03:42:38  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜13.8s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.2s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.7s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.3s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.6s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 03:45:45  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜13.7s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.2s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.2s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.4s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.3s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 14:41:25  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.5s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.2s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.2s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.5s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.6s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**

## 2026-10-08 14:45:58  runner
- 环境：带代理／无代理（本机有代理变量）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜13.8s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.6s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.7s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.6s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.5s
- 声明校验：通过｜零污染：通过
- **总判定：✅ 全绿**


## 2026-10-08 16:12:36  runner
- 环境：带代理／无代理（本机有代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.8s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.8s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.2s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.6s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜13.9s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜9.4s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜9.4s
- 声明校验：通过｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-08 16:14:09  runner
- 环境：当前环境（本机有代理变量）
- 验收级：否（`--env once` 单环境，**不可作为验收依据**）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.9s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.3s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.2s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.7s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.7s
- `smoke_frontend.py`（A1 · 当前环境）— 前端运行时 G1–G6｜断言 54｜9.4s
- 声明校验：通过｜零污染：通过
- 汇总：9 套 × 1 环境｜累计断言 727 次
- **总判定：❌ 有失败**

## 2026-10-08 16:16:42  runner
- 环境：当前环境（本机有代理变量）
- 验收级：否（`--env once` 单环境，**不可作为验收依据**）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.0s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.3s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.8s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜17.8s
- `smoke_frontend.py`（A1 · 当前环境）— 前端运行时 G1–G6｜断言 54｜9.6s
- 声明校验：**1 处不符**（常规 0 处 ＋ A1 1 处）｜零污染：通过
- 汇总：9 套 × 1 环境｜累计断言 727 次
- **总判定：❌ 有失败**

## 2026-10-08 16:19:29  runner
- 环境：带代理／无代理（本机有代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.5s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.0s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.7s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.7s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.3s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.7s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜13.8s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜9.5s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜9.5s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-08 16:46:21  runner
- 环境：带代理／无代理（本机有代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.5s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.6s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.9s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.7s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜13.1s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜10.1s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜9.7s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-08 17:19:05  runner
- 环境：带代理／无代理（本机有代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.4s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.7s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜11.1s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜13.0s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜9.9s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜9.7s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-08 18:03:57  runner
- 环境：带代理／无代理（本机有代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.3s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.6s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜14.5s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.5s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.8s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.8s
- `regression_restart.py` — 重启链路｜断言 n/a｜10.9s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜13.0s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜9.7s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜9.8s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-08 22:51:07  runner
- 环境：带代理／无代理（本机无代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.1s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.2s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜12.2s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.0s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.3s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.9s
- `regression_restart.py` — 重启链路｜断言 n/a｜9.0s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜11.4s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜6.8s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜6.6s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-08 23:13:50  runner
- 环境：带代理／无代理（本机无代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.1s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.1s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜12.2s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.0s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.2s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.4s
- `regression_restart.py` — 重启链路｜断言 n/a｜8.8s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜11.2s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜6.5s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜6.5s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-09 00:09:14  runner
- 环境：带代理／无代理（本机无代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.1s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.1s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜12.2s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.0s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.3s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.9s
- `regression_restart.py` — 重启链路｜断言 n/a｜9.1s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜11.3s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜7.1s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜6.6s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-09 02:24:48  runner
- 环境：带代理／无代理（本机无代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.1s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.2s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜12.1s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.0s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.2s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.9s
- `regression_restart.py` — 重启链路｜断言 n/a｜8.9s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜11.3s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜6.9s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜6.5s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-09 02:28:05  runner
- 环境：带代理／无代理（本机无代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.1s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.2s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜12.1s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜8.0s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜0.2s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.9s
- `regression_restart.py` — 重启链路｜断言 n/a｜9.0s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜11.2s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜6.6s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜6.5s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-09 02:37:07  runner
- 环境：带代理／无代理（本机有代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.4s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.7s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.5s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.9s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜1.0s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.0s
- `regression_restart.py` — 重启链路｜断言 n/a｜13.2s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.7s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜10.1s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜10.0s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-09 02:40:35  runner
- 环境：带代理／无代理（本机有代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.4s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.7s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.6s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.9s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜1.0s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜6.5s
- `regression_restart.py` — 重启链路｜断言 n/a｜13.2s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜14.1s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜9.5s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜9.3s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**

## 2026-10-09 03:44:48  runner
- 环境：带代理／无代理（本机有代理变量）
- 验收级：**是**（带代理／无代理双环境）
- A1 前端冒烟：已启用（`--with-a1`）
- `test_engine.py` — 规则引擎单测（零 IO）｜断言 114｜0.4s
- `test_collector.py` — 采集器离线部分（临时库）｜断言 47｜0.7s
- `test_api_contract.py` — 端点契约（沙箱 + 假 Analyzer）｜断言 97｜15.4s
- `test_finder_collect.py` — Finder 自身抓取（沙箱 + 假 B站，零实网）｜断言 22｜9.9s
- `test_capabilities.py` — 能力/门控/端点冒烟｜断言 289｜1.0s
- `regression_fallback.py` — 降级与回退链路｜断言 7｜5.5s
- `regression_restart.py` — 重启链路｜断言 n/a｜13.1s
- `verify_riskfix.py` — 风险修复验收（整进程隔离副本）｜断言 97｜15.4s
- `smoke_frontend.py`（A1 · 带代理）— 前端运行时 G1–G6｜断言 54｜9.7s
- `smoke_frontend.py`（A1 · 无代理）— 前端运行时 G1–G6｜断言 54｜9.4s
- 声明校验：通过（常规 0 处 ＋ A1 0 处）｜零污染：通过
- 汇总：9 套 × 2 环境｜累计断言 1454 次
- **总判定：✅ 全绿**
