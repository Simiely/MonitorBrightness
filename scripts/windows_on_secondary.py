# -*- coding: utf-8 -*-
"""只读：副屏上有没有窗口？决定能不能安全地做一次真实断开验证。

真实断开副屏时，Windows 会把副屏上的窗口挤到主屏，而且**接回后不会自动搬回去**。
所以做验证前先看有没有窗口：没有才动手，有就不碰用户的桌面。
"""
import ctypes
from ctypes import wintypes

MONITOR_DEFAULTTOPRIMARY = 1


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


def windows_outside_primary():
    u32 = ctypes.windll.user32
    hmon = u32.MonitorFromWindow(u32.GetDesktopWindow(), MONITOR_DEFAULTTOPRIMARY)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    if not u32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return None, []
    prim = mi.rcMonitor
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        r = RECT()
        if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
        if not (prim.left <= cx < prim.right and prim.top <= cy < prim.bottom):
            buf = ctypes.create_unicode_buffer(96)
            u32.GetWindowTextW(hwnd, buf, 96)
            found.append(buf.value)
        return True

    u32.EnumWindows(cb, 0)
    return (prim.left, prim.top, prim.right, prim.bottom), found


if __name__ == "__main__":
    box, names = windows_outside_primary()
    print("主屏矩形：%s" % (box,))
    if names is None:
        print("拿不到主屏信息")
    elif not names:
        print("副屏上没有窗口 —— 可以安全做真实断开验证")
    else:
        print("副屏上有 %d 个窗口，不该动：" % len(names))
        for n in names:
            print("   - %s" % n)
