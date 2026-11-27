# -*- coding: utf-8 -*-
"""R2：把 _ccd_query_raw 拆成「取原始缓冲」与「解析 rows」两段。

函数体逐字复用（只改返回值与函数边界），零行为变化。
"""
import ast
from pathlib import Path

P = Path("hw/ccd.py")


def main():
    src = P.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_ccd_query_raw")
    old = "".join(lines[node.lineno - 1:node.end_lineno])

    split = old.index("    rows = []")
    prefix, rows = old[:split], old[split:]
    quote = prefix.index('    flags = ')
    query_body = prefix[quote:].rstrip() + "\n    return paths, modes, pc.value, mc.value\n"
    rows_body = rows.rstrip()
    tail = "    return paths, modes, pc.value, mc.value, rows"
    assert rows_body.endswith(tail)
    rows_body = rows_body[:-len(tail)] + "    return rows\n"

    new = '''def _ccd_query_raw_paths(u32):
    """取 QueryDisplayConfig 的原始缓冲；(paths, modes, pc, mc)，失败 None。"""
''' + query_body + '''
def _ccd_query_rows(u32, paths):
    """把 paths 解析成未折叠的 rows（anchor/active/gdi/friendly/vdd）。"""
''' + rows_body + '''
def _ccd_query_raw(u32=None):
    """返回 (paths, modes, pc, mc, rows)；失败返回 None。

    rows 每条 path 一项（不折叠）：anchor / active / connected / gdi /
    friendly / vdd / pathIndex。paths + modes 是原始字节快照的来源。
    """
    u32 = u32 if u32 is not None else _ccd_api()
    if u32 is None:
        return None
    got = _ccd_query_raw_paths(u32)
    if got is None:
        return None
    paths, modes, pc, mc = got
    return paths, modes, pc, mc, _ccd_query_rows(u32, paths)
'''
    lines[node.lineno - 1:node.end_lineno] = [new]
    P.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("_ccd_query_raw 已拆为 _ccd_query_raw_paths / _ccd_query_rows / 薄壳")


if __name__ == "__main__":
    main()
