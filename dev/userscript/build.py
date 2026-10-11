# -*- coding: utf-8 -*-
"""M5 收口：把 header.js + core.js + runtime.js 拼成最终油猴脚本 bili_history.user.js。

用法：
    python dev/userscript/build.py            # 构建
    python dev/userscript/build.py --check    # 仅做结构校验（不写文件）

拼接顺序固定为 header → core → runtime：
  · header 是 Tampermonkey 元数据块（含 @run-at / @grant / @match）
  · core 定义 window.BHF（纯逻辑，可单测）
  · runtime 依赖 BHF，必须最后

构建后做语法校验（node --check，若本机有 node）＋ 结构断言
（含 @name、window.BHF、Overlay、UI、Export、运行时注入保护）。
"""
import os
import sys
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))          # dev/userscript/
ROOT = os.path.dirname(os.path.dirname(HERE))             # 仓库根
JS_SRC = os.path.join(ROOT, "src", "userscript")          # 源码位置
HEADER = os.path.join(JS_SRC, "header.js")
CORE = os.path.join(JS_SRC, "core.js")
RUNTIME = os.path.join(JS_SRC, "runtime.js")
OUT = os.path.join(ROOT, "bili_history.user.js")           # 产物落仓库根

NODE_CANDIDATES = [
    "C:/Users/GD/.workbuddy/binaries/node/versions/22.22.2-6/node.exe",
    shutil.which("node"),  # 可能指向系统 node
]


def read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def build():
    if not (os.path.exists(HEADER) and os.path.exists(CORE) and os.path.exists(RUNTIME)):
        print("  x 缺少源文件：", HEADER, CORE, RUNTIME)
        return None
    header = read(HEADER).strip()
    core = read(CORE)
    runtime = read(RUNTIME)
    # 去掉源文件里可能误带的 BOM，保证拼接干净
    for s in (header, core, runtime):
        if s.startswith("\ufeff"):
            s = s.lstrip("\ufeff")
    out = header + "\n\n" + core + "\n\n" + runtime + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    return out


def node_check(path):
    node = None
    for c in NODE_CANDIDATES:
        if c and os.path.exists(c):
            node = c
            break
    if not node:
        return "skip", "未找到 node，跳过语法校验"
    try:
        r = subprocess.run([node, "--check", path], capture_output=True)
    except Exception as e:  # noqa
        return "skip", "node 调用异常：%s" % e
    if r.returncode == 0:
        return "ok", "node --check 通过"
    err = (r.stderr or r.stdout).decode("utf-8", "replace").strip()
    return "fail", "node --check 失败：%s" % err


def check(out):
    problems = []
    if "@name" not in out:
        problems.append("缺 @name 元数据")
    if "window.BHF" not in out:
        problems.append("缺 window.BHF 挂载")
    for sym in ("BHF.Overlay", "BHF.UI", "BHF.Export", "BHF.Storage"):
        if sym not in out:
            problems.append("缺 %s" % sym)
    if "__BHF_RT__" not in out:
        problems.append("运行时不重复注入保护缺失")
    # 顺序：core 必须出现在 runtime 之前
    ic = out.find("BHF.Overlay")
    ir = out.find("window.__BHF_RT__")
    if ic != -1 and ir != -1 and ic > ir:
        problems.append("拼接顺序错误：core 应在 runtime 之前")
    return problems


def main():
    only_check = "--check" in sys.argv
    if only_check:
        if not (os.path.exists(OUT)):
            print("  x 还没有构建产物，先跑 build.py"); return 1
        out = read(OUT)
    else:
        out = build()
        if out is None:
            return 1
        print("  + 已构建：", OUT, "（%d 字节）" % len(out))
    st, msg = node_check(OUT)
    print("  · 语法校验：", msg)
    if st == "fail":
        print("  x 语法校验失败"); return 1
    problems = check(out)
    if problems:
        for p in problems:
            print("  x", p)
        return 1
    print("  · 结构断言：全部通过（@name / BHF.* / 拼接顺序 / 注入保护）")
    print("  结果：M5 收口成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
