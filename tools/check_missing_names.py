# -*- coding: utf-8 -*-
"""找出模块里"用了但没定义/没导入"的名字（轻量版 pyflakes）。

用法：python check_missing_names.py ui/sheets.py ui/widgets.py ...
"""
import ast
import builtins
import sys


# Python 自动注入的模块级名字不是 builtins 的属性，轻量扫描要单独认。
_MODULE_DUNDERS = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__annotations__", "__cached__",
}


def collect(tree):
    defined = set(dir(builtins)) | _MODULE_DUNDERS
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            # 函数参数也算定义（简化：只收 args）
            if hasattr(node, "args"):
                for a in list(node.args.args) + list(node.args.kwonlyargs):
                    defined.add(a.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    defined.add(a.asname or a.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            pass
    return defined


def main(paths):
    bad = 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=p)
        defined = collect(tree)
        missing = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in defined:
                    missing.setdefault(node.id, node.lineno)
        if missing:
            bad += 1
            print("× %s" % p)
            for name, ln in sorted(missing.items(), key=lambda x: x[1]):
                print("    L%-5d %s" % (ln, name))
        else:
            print("✓ %s" % p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
