# -*- coding: utf-8 -*-
"""窗口跟随（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

交接主屏后把应用窗口搬回它原本所在的屏；只认 Win32，不碰 Tk。
"""
import ctypes
import os
from ctypes import wintypes

# ============================================================================
# 窗口跟随（v2.01.9）
# ----------------------------------------------------------------------------
# 拓扑一变，Windows 会把各应用的窗口挤到"还能显示"的地方，而且**不会**在
# 拓扑恢复后搬回来。用户看到的就是"一改显示器设置，窗口全乱了"。
#
# 这里只在**一次操作内**做：动作前记下每个窗口属于哪块屏，动作后按这块屏的
# 平移量把窗口跟着搬。不落盘、不跨操作 —— 跨操作记录会跟用户后来的手动摆放
# 打架（他挪过的窗口会被我们又搬回去）。
# ============================================================================
_SWP_NOSIZE = 0x0001

_SWP_NOZORDER = 0x0004

_SWP_NOACTIVATE = 0x0010

_GWL_EXSTYLE = -20

_WS_EX_TOOLWINDOW = 0x00000080

_WS_EX_NOACTIVATE = 0x08000000

class _WinRect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def _user32():
    return ctypes.windll.user32 if os.name == "nt" else None

def _window_rect(u32, hwnd):
    r = _WinRect()
    if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top),
            int(r.right - r.left), int(r.bottom - r.top))

def iter_app_windows(u32=None):
    """枚举"用户看得见的应用窗口"。

    排除：不可见、最小化、无标题、工具窗/不激活窗（输入法、悬浮条）、
    本进程自己的窗口。收起的窗口不碰 —— 动它等于把它还原，那是另一回事。
    """
    u32 = u32 or _user32()
    if u32 is None:
        return []
    out = []
    me = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp):
        try:
            if not u32.IsWindowVisible(hwnd) or u32.IsIconic(hwnd):
                return True
            if u32.GetWindowTextLengthW(hwnd) == 0:
                return True
            ex = u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            if ex & (_WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE):
                return True
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == me:
                return True
            out.append(int(hwnd))
        except Exception:
            pass
        return True

    u32.EnumWindows(_cb, 0)
    return out

def win_snapshot(layout, u32=None):
    """记下每个应用窗口现在落在哪块屏上。layout = {锚点: (x, y, w, h)}。"""
    u32 = u32 or _user32()
    if u32 is None or not layout:
        return []
    snap = []
    for hwnd in iter_app_windows(u32):
        rect = _window_rect(u32, hwnd)
        if not rect:
            continue
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        for anchor, (x, y, w, h) in layout.items():
            if x <= cx < x + w and y <= cy < y + h:
                snap.append({"hwnd": hwnd, "rect": rect, "anchor": anchor})
                break
    return snap

def win_follow(snap, shift, dropped, u32=None):
    """按屏的平移量把窗口搬到新位置；所在屏没了的不动（交给 Windows）。

    只改位置不改大小，并且**只在窗口确实被挪走时才动**：如果它已经在目标
    位置，说明 Windows 没碰它，我们也不碰。
    """
    u32 = u32 or _user32()
    if u32 is None or not snap or tuple(shift) == (0, 0):
        return 0
    dropped = set(dropped or ())
    moved = 0
    for it in snap:
        if it["anchor"] in dropped:
            continue
        hwnd = it["hwnd"]
        try:
            if not u32.IsWindow(hwnd):
                continue
            want = (it["rect"][0] + shift[0], it["rect"][1] + shift[1])
            cur = _window_rect(u32, hwnd)
            if cur is None or (cur[0], cur[1]) == want:
                continue
            u32.SetWindowPos(hwnd, 0, want[0], want[1], 0, 0,
                             _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
            moved += 1
        except Exception:
            continue
    return moved
