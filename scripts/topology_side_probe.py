# -*- coding: utf-8 -*-
"""阶段 0 副作用探针：只读查看 CCD 数据库当前条目 + 可见窗口矩形。

用途（都不改显示设置）：
  --db       QueryDisplayConfig(QDC_DATABASE_CURRENT) 看数据库里记的拓扑
  --windows  枚举可见顶层窗口的标题与 rect，写入 JSON 供 off/on 前后比对

产物默认写工作区（含窗口标题，属本地信息，不入库）。
"""

import argparse
import ctypes
import json
import os
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topology_probe as tp

QDC_DATABASE_CURRENT = 0x00000004
WORK_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def query_db(u32):
    flags = QDC_DATABASE_CURRENT
    for _ in range(5):
        pc, mc = wintypes.UINT(), wintypes.UINT()
        rc = u32.GetDisplayConfigBufferSizes(flags, ctypes.byref(pc), ctypes.byref(mc))
        if rc != tp.ERROR_SUCCESS:
            return rc, None, []
        paths = (tp.PATH_INFO * pc.value)()
        modes = (tp.MODE_INFO * mc.value)()
        topo = wintypes.UINT()
        rc = u32.QueryDisplayConfig(flags, ctypes.byref(pc), ctypes.byref(paths),
                                    ctypes.byref(mc), ctypes.byref(modes),
                                    ctypes.byref(topo))
        if rc == tp.ERROR_SUCCESS:
            rows = []
            for p in list(paths)[:pc.value]:
                rows.append({
                    "active": bool(p.flags & tp.PATH_ACTIVE),
                    "targetAvailable": bool(p.targetInfo.targetAvailable),
                    "sourceId": p.sourceInfo.id,
                    "targetId": p.targetInfo.id,
                    "adapterLuid": tp.luid_str(p.sourceInfo.adapterId),
                    "sourceModeIdx": tp.mode_idx_text(p.sourceInfo.modeInfoIdx),
                    "targetModeIdx": tp.mode_idx_text(p.targetInfo.modeInfoIdx),
                })
            return rc, topo.value, rows
        if rc != 122:
            return rc, None, []
    return 122, None, []


def windows():
    u32 = ctypes.windll.user32
    u32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsIconic.argtypes = [wintypes.HWND]
    rows = []

    def cb(hwnd, lparam):
        if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
            return 1
        buf = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value.strip()
        if not title:
            return 1
        rc = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rc))
        rows.append({"title": title, "hwnd": hex(hwnd),
                     "rect": [rc.left, rc.top, rc.right, rc.bottom]})
        return 1

    proto = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, ctypes.c_void_p)
    u32.EnumWindows(proto(cb), None)
    return rows


def main():
    ap = argparse.ArgumentParser(description="CCD 数据库 / 窗口矩形只读探针")
    ap.add_argument("--db", action="store_true", help="查询 QDC_DATABASE_CURRENT")
    ap.add_argument("--windows", action="store_true", help="枚举可见窗口矩形")
    ap.add_argument("--out", help="JSON 输出文件（默认只打印）")
    args = ap.parse_args()
    if not args.db and not args.windows:
        ap.print_help()
        return 2

    result = {}
    if args.db:
        u32, _ = tp._api()
        rc, topo, rows = query_db(u32)
        result["database"] = {"rc": rc, "topologyId": topo, "paths": rows}
        print("QDC_DATABASE_CURRENT rc=%d topologyId=%s active=%d/%d" %
              (rc, topo, sum(1 for r in rows if r["active"]), len(rows)))
        for r in rows:
            if r["active"]:
                print("  ACTIVE adapter=%s src=%d tgt=%d srcIdx=%s" %
                      (r["adapterLuid"], r["sourceId"], r["targetId"], r["sourceModeIdx"]))
    if args.windows:
        result["windows"] = windows()
        print("可见窗口 %d 个" % len(result["windows"]))
        for w in result["windows"][:12]:
            print("  %-40s %s" % (w["title"][:40], w["rect"]))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("[已写入] %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
