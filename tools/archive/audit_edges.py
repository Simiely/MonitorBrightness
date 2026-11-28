# -*- coding: utf-8 -*-
"""打印可疑跨模块引用的具体名字（只读）。"""
import ast, sys, io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
src = (ROOT / "monitor_brightness.py").read_text(encoding="utf-8")
tree = ast.parse(src)

# 只关心这些可疑边
SUSPECT = {
    ("core.logging", "core.config"),
    ("core.config", "cli"),
    ("schedule", "cli"),
    ("hw.power", "schedule"),
    ("hw.topology", "hw.power"),
    ("hw.power", "hw.topology"),
    ("app", "hw.ccd"),
}
# 名字归属（与 audit_layers 相同，略去完整表，仅反查用）
import importlib.util
spec = importlib.util.spec_from_file_location("audit_layers", ROOT / "tools" / "audit_layers.py")

# 直接重新定义关键映射：从 audit_layers.py 源码里抠 GROUPS 太绕，这里手动维护可疑边两侧的名字集合
# 简单方案：把 audit_layers 的 GROUPS 赋值段执行出来
gl_src = (ROOT / "tools" / "audit_layers.py").read_text(encoding="utf-8")
ns = {}
start = gl_src.index("GROUPS = {}")
end = gl_src.index("# 建立")
exec(gl_src[start:end], ns)
GROUPS = ns["GROUPS"]

owner = {}
for node in tree.body:
    names = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names = [node.name]
    for n in names:
        if n in GROUPS:
            owner[n] = GROUPS[n]

for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        continue
    g = GROUPS.get(node.name)
    if not g:
        continue
    used = {}
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id in GROUPS:
            tg = GROUPS[sub.id]
            if (g, tg) in SUSPECT:
                used.setdefault(sub.id, set()).add(sub.lineno)
    if used:
        print(f"[{g}] {node.name}  引用:")
        for name, lns in sorted(used.items()):
            print(f"    {name:35} L{sorted(lns)[:8]}")
