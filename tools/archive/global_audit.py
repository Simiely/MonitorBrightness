# -*- coding: utf-8 -*-
"""全局审计（只读）：结构、大函数、分组缠绕、跨模块依赖。"""
import ast, sys, io
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

def span(node):
    return (node.lineno, getattr(node, "end_lineno", node.lineno))

def lines(n):
    a, b = span(n)
    return b - a + 1

def audit_file(path):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = []   # (kind, qualname, start, end, nlines, lineno_of_def)
    classes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(("func", node.name, *span(node), lines(node)))
        elif isinstance(node, ast.ClassDef):
            cls_line = lines(node)
            classes.append((node.name, *span(node), cls_line))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs.append(("meth", f"{node.name}.{sub.name}", *span(sub), lines(sub)))
    return src, funcs, classes

print("=" * 72)
print("1) monitor_brightness.py 顶层结构（按出现顺序）")
print("=" * 72)
src, funcs, classes = audit_file(ROOT / "monitor_brightness.py")
total = src.count("\n") + 1
print(f"总行数: {total}\n")
print(f"{'类型':6} {'名称':40} {'起始':>6} {'结束':>6} {'行数':>5}")
for kind, name, a, b, n in funcs:
    flag = "  <<<" if n >= 100 else ""
    print(f"{kind:6} {name:40} {a:6} {b:6} {n:5}{flag}")
print(f"\n类：")
for name, a, b, n in classes:
    print(f"  {name:20} {a:6} {b:6} {n:5}")

# 大函数排行（整个仓库）
print()
print("=" * 72)
print("2) 全仓库 >=60 行的函数/方法（大函数排行）")
print("=" * 72)
allf = []
for p in [ROOT / "monitor_brightness.py", *sorted((ROOT / "ui").glob("*.py")),
          *sorted((ROOT / "domain").glob("*.py")), *sorted((ROOT / "core").glob("*.py"))]:
    _, fs, _ = audit_file(p)
    for kind, name, a, b, n in fs:
        if n >= 60:
            allf.append((n, p.name, kind, name, a))
allf.sort(reverse=True)
for n, fname, kind, name, a in allf[:40]:
    print(f"{n:5} 行  {fname:24} {name}{f'  (L{a})'}")

# 主文件按主题分组估算（根据函数名关键词）
print()
print("=" * 72)
print("3) monitor_brightness.py 模块级函数按主题分组的行数估算")
print("=" * 72)
groups = {
    "Win32/ctypes 声明与探针(ddc/topology/ccd/system)": ("ddc", "topology", "ccd", "system_", "probe", "vcp", "getphysical", "displayconfig", "dxva2"),
    "计划任务(schedule/task/schtask)": ("schedule", "task", "slot", "weekday", "register_script", "apply_schedule"),
    "配置(config/settings/autostart)": ("config", "settings", "autostart", "load_", "save_", "migrate"),
    "事件/日志(events/log/diag/error)": ("event", "log", "diag", "error", "dlog"),
    "主入口/CLI": ("main", "cli", "selftest", "argparse", "parse_args"),
}
gsize = defaultdict(int)
gcount = defaultdict(int)
mapped = set()
for kind, name, a, b, n in funcs:
    if kind != "func":
        continue
    low = name.lower()
    for g, keys in groups.items():
        if any(k in low for k in keys):
            gsize[g] += n; gcount[g] += 1; mapped.add(name)
            break
all_func_lines = sum(n for k, *_ , n in funcs if k == "func")
print(f"模块级函数总行数: {all_func_lines}")
for g, keys in groups.items():
    print(f"  {g:55} {gsize[g]:5} 行 / {gcount[g]} 个")
unmapped = [ (name, n) for kind, name, a, b, n in funcs if kind=="func" and name not in mapped and n>=20 ]
unmapped.sort(key=lambda x:-x[1])
print(f"\n未归类的 >=20 行模块级函数（{sum(n for _,n in unmapped)} 行）:")
for name, n in unmapped[:25]:
    print(f"  {name:45} {n:5}")

# 跨模块依赖：ui/ 与 domain/ 引用了主文件的哪些名字
print()
print("=" * 72)
print("4) ui/、domain/ 对主文件的依赖面（import 了哪些名字）")
print("=" * 72)
for p in sorted((ROOT / "ui").glob("*.py")) + sorted((ROOT / "domain").glob("*.py")):
    tree = ast.parse(p.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "monitor_brightness":
            names.update(a.name for a in node.names)
    print(f"  {p.name:14} <- monitor_brightness: {len(names)} 个名字")
    if names:
        print(f"    {sorted(names)}")

# App / mixin 方法数与行数汇总
print()
print("=" * 72)
print("5) App 体系现状")
print("=" * 72)
app_funcs = [f for f in funcs if f[1].split(".")[0].startswith(("_", "App"))]
for cls in classes:
    meths = [f for f in funcs if f[0] == "meth" and f[1].startswith(cls[0] + ".")]
    tot = sum(f[4] for f in meths)
    print(f"  {cls[0]:18} {len(meths):3} 方法  {tot:5} 行  (类定义 {cls[3]} 行)")
