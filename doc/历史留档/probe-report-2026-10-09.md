# BHF 探针诊断报告

- 生成时间：2026-10-09T11:45:53.964Z（探针 v0.2.0）
- 页面：https://www.bilibili.com/history?spm_id_from=333.1007.0.0
- 在历史页：true　已登录：true

## 结论

✅ K5 通过：稳定拦到 8 条

- 原因：hook 装载、请求捕获、解析、业务码 全部正常
- 建议：把报告贴给开发者，即可进入 M0（IndexedDB 双存储）

## 分步结果

| 步骤 | 结果 | 详情 |
| --- | --- | --- |
| 1. hook 装载 | 通过 | fetch=true xhr=true |
| 2. 见到目标请求 | 通过 | 次数=8 |
| 3. 捕获到响应 | 通过 | 条数=8 |
| 4. JSON 解析 | 通过 | 正常 |
| 5. 业务码 code=0 | 通过 | code=[0,0,0,0,0,0,0,0] |

## 计数

```json
{"hits":8,"seenTargetRequests":8,"seenTotal":30}
```

## 抓到的响应（最多 5 条）

```json
[
 {
  "t": 296,
  "via": "fetch",
  "url": "//api.bilibili.com/x/web-interface/history/cursor?ps=20&type=archive&web_location=333.1391&x-bili-locale-json=%7B%22c_locale%22:%7B%22language%22:%22zh%22,%22script%22:%22Hans%22%7D,%22always_translat",
  "status": 200,
  "bytes": 14049,
  "code": 0,
  "listLen": 20,
  "cursor": {
   "max": 117314867432857,
   "view_at": 1791536669,
   "business": "archive",
   "ps": 20
  },
  "topKeys": [
   "author_face",
   "author_mid",
   "author_name",
   "badge",
   "cover",
   "covers",
   "current",
   "duration",
   "history",
   "is_fav",
   "is_finish",
   "kid",
   "live_status",
   "long_title",
   "new_desc",
   "progress",
   "show_title",
   "tag_name",
   "title",
   "total",
   "uri",
   "videos",
   "view_at"
  ],
  "hasHistorySub": true,
  "historyKeys": [
   "business",
   "bvid",
   "cid",
   "dt",
   "epid",
   "oid",
   "page",
   "part"
  ],
  "parseError": null
 },
 {
  "t": 12440,
  "via": "fetch",
  "url": "//api.bilibili.com/x/web-interface/history/cursor?max=117314867432857&view_at=1791536669&business=archive&ps=20&type=all&web_location=333.1391&x-bili-locale-json=%7B%22c_locale%22:%7B%22language%22:%2",
  "status": 200,
  "bytes": 13941,
  "code": 0,
  "listLen": 20,
  "cursor": {
   "max": 114278409440980,
   "view_at": 1791470880,
   "business": "archive",
   "ps": 20
  },
  "topKeys": [
   "author_face",
   "author_mid",
   "author_name",
   "badge",
   "cover",
   "covers",
   "current",
   "duration",
   "history",
   "is_fav",
   "is_finish",
   "kid",
   "live_status",
   "long_title",
   "new_desc",
   "progress",
   "show_title",
   "tag_name",
   "title",
   "total",
   "uri",
   "videos",
   "view_at"
  ],
  "hasHistorySub": true,
  "historyKeys": [
   "business",
   "bvid",
   "cid",
   "dt",
   "epid",
   "oid",
   "page",
   "part"
  ],
  "parseError": null
 },
 {
  "t": 40804,
  "via": "fetch",
  "url": "//api.bilibili.com/x/web-interface/history/cursor?max=114278409440980&view_at=1791470880&business=archive&ps=20&type=all&web_location=333.1391&x-bili-locale-json=%7B%22c_locale%22:%7B%22language%22:%2",
  "status": 200,
  "bytes": 14535,
  "code": 0,
  "listLen": 20,
  "cursor": {
   "max": 116800142509360,
   "view_at": 1791452079,
   "business": "archive",
   "ps": 20
  },
  "topKeys": [
   "author_face",
   "author_mid",
   "author_name",
   "badge",
   "cover",
   "covers",
   "current",
   "duration",
   "history",
   "is_fav",
   "is_finish",
   "kid",
   "live_status",
   "long_title",
   "new_desc",
   "progress",
   "show_title",
   "tag_name",
   "title",
   "total",
   "uri",
   "videos",
   "view_at"
  ],
  "hasHistorySub": true,
  "historyKeys": [
   "business",
   "bvid",
   "cid",
   "dt",
   "epid",
   "oid",
   "page",
   "part"
  ],
  "parseError": null
 },
 {
  "t": 65415,
  "via": "fetch",
  "url": "//api.bilibili.com/x/web-interface/history/cursor?max=116800142509360&view_at=1791452079&business=archive&ps=20&type=all&web_location=333.1391&x-bili-locale-json=%7B%22c_locale%22:%7B%22language%22:%2",
  "status": 200,
  "bytes": 14340,
  "code": 0,
  "listLen": 20,
  "cursor": {
   "max": 117397075854112,
   "view_at": 1791403275,
   "business": "archive",
   "ps": 20
  },
  "topKeys": [
   "author_face",
   "author_mid",
   "author_name",
   "badge",
   "cover",
   "covers",
   "current",
   "duration",
   "history",
   "is_fav",
   "is_finish",
   "kid",
   "live_status",
   "long_title",
   "new_desc",
   "progress",
   "show_title",
   "tag_name",
   "title",
   "total",
   "uri",
   "videos",
   "view_at"
  ],
  "hasHistorySub": true,
  "historyKeys": [
   "business",
   "bvid",
   "cid",
   "dt",
   "epid",
   "oid",
   "page",
   "part"
  ],
  "parseError": null
 },
 {
  "t": 66025,
  "via": "fetch",
  "url": "//api.bilibili.com/x/web-interface/history/cursor?max=117397075854112&view_at=1791403275&business=archive&ps=20&type=all&web_location=333.1391&x-bili-locale-json=%7B%22c_locale%22:%7B%22language%22:%2",
  "status": 200,
  "
```

## 出现过哪些请求（去重 top25，`isHistory` 为 true 的尤其重要）

```json
[
 {
  "path": "//data.bilibili.com/v2/log/web",
  "n": 13,
  "isHistory": false
 },
 {
  "path": "//api.bilibili.com/x/web-interface/history/cursor",
  "n": 8,
  "isHistory": true
 },
 {
  "path": "//api.vc.bilibili.com/link_setting/v1/link_setting/get",
  "n": 1,
  "isHistory": false
 },
 {
  "path": "https://api.bilibili.com/x/kv-frontend/namespace/data",
  "n": 1,
  "isHistory": false
 },
 {
  "path": "//api.vc.bilibili.com/x/im/web/msgfeed/unread",
  "n": 1,
  "isHistory": false
 },
 {
  "path": "//api.vc.bilibili.com/session_svr/v1/session_svr/single_unread",
  "n": 1,
  "isHistory": false
 },
 {
  "path": "data:application/wasm;base64,AGFzbQEAAAABzAItYAJ/fwF/YAJ/fwBgA39/fwBgA39/fwF/YAF/AGABfwF/YAR/f39/AGAFf39/f38AYAABf2AGf39",
  "n": 1,
  "isHistory": false
 },
 {
  "path": "//api.bilibili.com/x/polymer/web-dynamic/v1/feed/nav",
  "n": 1,
  "isHistory": false
 },
 {
  "path": "https://i1.hdslb.com/bfs/svg-next/BDC/arrow_forward_right_line/v3.json",
  "n": 1,
  "isHistory": false
 },
 {
  "path": "https://i1.hdslb.com/bfs/svg-next/BDC/arrow_play_next_line/v1.json",
  "n": 1,
  "isHistory": false
 },
 {
  "path": "//data.bilibili.com/log/web",
  "n": 1,
  "isHistory": false
 }
]
```

## 时间线（毫秒，相对脚本启动）

```
0ms  探针已启动 fetch=true xhr=true
193ms  目标请求 #1 (//api.bilibili.com/x/web-interface/history/cursor?ps=20&type=archive&web_locatio)
296ms  捕获响应 via=fetch code=0 list=20
12358ms  目标请求 #2 (//api.bilibili.com/x/web-interface/history/cursor?max=117314867432857&view_at=17)
12440ms  捕获响应 via=fetch code=0 list=20
40702ms  目标请求 #3 (//api.bilibili.com/x/web-interface/history/cursor?max=114278409440980&view_at=17)
40804ms  捕获响应 via=fetch code=0 list=20
65296ms  目标请求 #4 (//api.bilibili.com/x/web-interface/history/cursor?max=116800142509360&view_at=17)
65415ms  捕获响应 via=fetch code=0 list=20
65939ms  目标请求 #5 (//api.bilibili.com/x/web-interface/history/cursor?max=117397075854112&view_at=17)
66026ms  捕获响应 via=fetch code=0 list=20
67287ms  目标请求 #6 (//api.bilibili.com/x/web-interface/history/cursor?max=117315924398371&view_at=17)
67452ms  捕获响应 via=fetch code=0 list=20
68284ms  目标请求 #7 (//api.bilibili.com/x/web-interface/history/cursor?max=117397579110261&view_at=17)
68367ms  捕获响应 via=fetch code=0 list=20
69125ms  目标请求 #8 (//api.bilibili.com/x/web-interface/history/cursor?max=117396706692017&view_at=17)
69211ms  捕获响应 via=fetch code=0 list=20
```