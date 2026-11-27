# -*- coding: utf-8 -*-
"""R2 收尾：把 CCD 几何/布局辅助从 hw/topology.py 拆到 hw/geometry.py。

让 topology.py 落在 700 行硬线以内；函数体逐字搬，只调整 import。
"""
import ast
from pathlib import Path

SRC = Path("hw/topology.py")
DST = Path("hw/geometry.py")

NAMES = [
    "_ccd_preferred_size", "_ccd_live_size", "_ccd_layout_rects",
    "_ccd_layout_by_anchor", "_ccd_free_origin", "_ccd_place",
    "_ccd_append_source_mode", "_ccd_capture_positions",
    "_ccd_fill_missing_source_modes", "_ccd_apply_plan_on", "_fold_raw_rows",
]

HEADER = '''# -*- coding: utf-8 -*-
"""CCD 几何与布局辅助（v2.03.0 R2，从 hw/topology.py 逐字拆出）。

位置读写、source mode 合成、快照/应用等纯几何操作；拓扑状态机在 hw/topology.py。
"""
import ctypes
from ctypes import wintypes

from core.logging import dlog
from hw.ccd import _ccd_apply
from hw.ccd_structs import (
    _CCD_MODE_IDX_INVALID, _CCD_PATH_ACTIVE, _CCD_PIXELFORMAT_NONGDI,
    _CCD_TYPE_SOURCE, _CcdModeInfo, _CcdTargetPreferredMode,
    _DC_GET_TARGET_PREFERRED_MODE,
)

'''

TOPO_IMPORT = '''from hw.geometry import (
    _ccd_append_source_mode, _ccd_apply_plan_on, _ccd_capture_positions,
    _ccd_fill_missing_source_modes, _ccd_free_origin, _ccd_live_size,
    _ccd_layout_by_anchor, _ccd_layout_rects, _ccd_place,
    _ccd_preferred_size, _fold_raw_rows,
)
'''


def main():
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    picked = []
    ranges = []
    for nm in NAMES:
        n = nodes.get(nm)
        if n is None:
            raise SystemExit("缺函数: %s" % nm)
        start = n.lineno - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        picked.append((n.lineno, "".join(lines[start:n.end_lineno])))
        ranges.append((start, n.end_lineno))
    picked.sort()
    DST.write_text(HEADER + "\n\n".join(c.rstrip("\n") for _, c in picked) + "\n",
                   encoding="utf-8", newline="\n")

    for a, b in sorted(ranges, reverse=True):
        del lines[a:b]
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("from hw.winfollow import "))
    lines.insert(idx + 1, TOPO_IMPORT)
    SRC.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("geometry：搬移 %d 个函数" % len(NAMES))


if __name__ == "__main__":
    main()
