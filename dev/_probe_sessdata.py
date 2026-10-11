# -*- coding: utf-8 -*-
"""B5 辅助：对比 Analyzer 与 Finder 两侧的 SESSDATA 状态（只读）。"""
import io
import os
import re
import json
import hashlib

FILLER = chr(92) + "s"   # 正则里的 \s，避免 heredoc/bash 吞掉


def probe(path, label, is_json):
    print("=== %s ===" % label)
    if not os.path.exists(path):
        print("  文件不存在:", path)
        return None
    s = io.open(path, encoding="utf-8", errors="replace").read()
    if is_json:
        try:
            v = (json.loads(s).get("sessdata") or "")
        except Exception as e:
            print("  JSON 解析失败:", e)
            return None
    else:
        m = re.search(r"sessdata:" + FILLER + r'*["\']?([^' + FILLER + r'"\']+)', s, re.I)
        v = m.group(1) if m else ""
    if not v:
        print("  长度 = 0  →  **未配置**")
        return ""
    print("  长度 = %d | sha256_12 = %s" % (len(v), hashlib.sha256(v.encode()).hexdigest()[:12]))
    print("  形态 = %s" % ("合法纯值" if (len(v) > 50 and " " not in v.strip()) else "可疑（含空格/过短）"))
    return v


a = probe(r"D:\Program Files (x86)\BilibiliHistoryAnalyzer\config\config.yaml", "Analyzer config.yaml", False)
print()
f = probe(r"D:\Programs\Share\BilibiliHistoryFinder\config.json", "Finder config.json", True)
print()
print("=== 结论 ===")
if a and f:
    print("  两侧**相同** →", a == f)
elif a and not f:
    print("  ⚠️ Analyzer 有、Finder 无 → 独立形态抓取会 blocked")
elif f and not a:
    print("  ⚠️ Finder 有、Analyzer 无 → Analyzer 侧抓取会 -101")
else:
    print("  两侧都无")
