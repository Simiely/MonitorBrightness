# -*- coding: utf-8 -*-
"""SetDisplayConfig 的 ctypes 结构体与常量（v2.03.0 R2）。

纯声明，零依赖；所有 CCD 相关模块共用这一份定义。
"""
import ctypes
from ctypes import wintypes

# ---- CCD 只读拓扑（拓扑模式的状态层；只查不写）-------------------------
# 依据：Windows CCD API（QueryDisplayConfig / DisplayConfigGetDeviceInfo）。
# 只用于"现在这块屏在不在桌面拓扑里"的判断；真正的断开/接回在阶段 E3 才接入。
class _CcdLuid(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

class _CcdRational(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.DWORD), ("Denominator", wintypes.DWORD)]

class _CcdSourceInfo(ctypes.Structure):
    _fields_ = [("adapterId", _CcdLuid), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]

class _CcdTargetInfo(ctypes.Structure):
    _fields_ = [("adapterId", _CcdLuid), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("outputTechnology", wintypes.UINT),
                ("rotation", wintypes.UINT), ("scaling", wintypes.UINT),
                ("refreshRate", _CcdRational), ("scanLineOrdering", wintypes.UINT),
                ("targetAvailable", ctypes.c_int), ("statusFlags", wintypes.UINT)]

class _CcdPathInfo(ctypes.Structure):
    _fields_ = [("sourceInfo", _CcdSourceInfo), ("targetInfo", _CcdTargetInfo),
                ("flags", wintypes.UINT)]

class _CcdPoint(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

class _CcdSourceMode(ctypes.Structure):
    _fields_ = [("width", wintypes.UINT), ("height", wintypes.UINT),
                ("pixelFormat", wintypes.UINT), ("position", _CcdPoint),
                ("reserved", wintypes.UINT)]

class _CcdModeInfo(ctypes.Structure):
    _fields_ = [("infoType", wintypes.UINT), ("id", wintypes.UINT),
                ("adapterId", _CcdLuid), ("sourceMode", _CcdSourceMode),
                ("tail", ctypes.c_uint64 * 3)]

class _CcdInfoHeader(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", _CcdLuid), ("id", wintypes.UINT)]

class _CcdSourceName(ctypes.Structure):
    _fields_ = [("header", _CcdInfoHeader),
                ("viewGdiDeviceName", wintypes.WCHAR * 32)]

class _CcdTargetName(ctypes.Structure):
    _fields_ = [("header", _CcdInfoHeader), ("flags", wintypes.UINT),
                ("outputTechnology", wintypes.UINT),
                ("edidManufactureId", wintypes.WORD),
                ("edidProductCodeId", wintypes.WORD),
                ("connectorInstance", wintypes.UINT),
                ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                ("monitorDevicePath", wintypes.WCHAR * 128)]

class _Ccd2D(ctypes.Structure):
    _fields_ = [("cx", wintypes.UINT), ("cy", wintypes.UINT)]

class _CcdVideoSignalInfo(ctypes.Structure):
    _fields_ = [("pixelRate", ctypes.c_uint64),
                ("hSyncFreq", _CcdRational), ("vSyncFreq", _CcdRational),
                ("activeSize", _Ccd2D), ("totalSize", _Ccd2D),
                ("videoStandard", wintypes.UINT),
                ("scanLineOrdering", wintypes.UINT)]

class _CcdTargetPreferredMode(ctypes.Structure):
    """DISPLAYCONFIG_TARGET_PREFERRED_MODE：这块屏的首选分辨率。"""

    _fields_ = [("header", _CcdInfoHeader), ("width", wintypes.UINT),
                ("height", wintypes.UINT), ("targetMode", _CcdVideoSignalInfo)]

_QDC_ALL_PATHS = 0x1

_QDC_VIRTUAL_MODE_AWARE = 0x10

_CCD_PATH_ACTIVE = 0x1

_CCD_GET_SOURCE_NAME = 1

_CCD_GET_TARGET_NAME = 2

_DC_GET_TARGET_PREFERRED_MODE = 3          # DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_PREFERRED_MODE

_CCD_PIXELFORMAT_NONGDI = 4                # Windows 给 source mode 用的就是它，不是 32BPP

_CCD_EDID_IDS_VALID = 0x4          # DISPLAYCONFIG_TARGET_DEVICE_NAME_FLAGS.edidIdsValid

_CCD_TARGET_IN_USE = 0x1          # DISPLAYCONFIG_TARGET_IN_USE

# ---- 拓扑动作层（E3）-----------------------------------------------------
# v2.04 起熄灭固定走拓扑，这些结构体就是唯一的开关屏路径。
# 事实依据见 docs/拓扑探针.md C2–C5：
#   - 副屏断开 rc=0 / 805ms，接回 986ms（含进程启动）；
#   - 主屏断开 rc=87，Windows 不允许直接移除主屏；
#   - inactive 时 DDC 读不到，所以「点亮」必须先恢复拓扑、再写亮度；
#   - 回读失败时原始字节快照可原样恢复（C5 强制失败实验验证过）。
# 快照落在 %APPDATA%\MonitorBrightness\topology-snapshot.json（仓库外，不入库）。
_SDC_USE_SUPPLIED = 0x00000020

_SDC_APPLY = 0x00000080

_SDC_SAVE_TO_DATABASE = 0x00000200

_SDC_ALLOW_CHANGES = 0x00000400

_SDC_VIRTUAL_MODE_AWARE = 0x00008000

_CCD_APPLY_FLAGS = (_SDC_USE_SUPPLIED | _SDC_APPLY | _SDC_SAVE_TO_DATABASE |
                    _SDC_ALLOW_CHANGES | _SDC_VIRTUAL_MODE_AWARE)

_CCD_MODE_IDX_INVALID = 0xFFFFFFFF

TOPO_VERIFY_TIMEOUT = 2.0          # D3：接回回读有界轮询 ≤2s

# ---- 声明式接回（v2.01.0）------------------------------------------------
# ⚠️ 设计纠偏：接回**不再恢复"断开前的历史快照"**。
# 快照是"过去某一刻的整份拓扑字节"，一旦期间用户在 Windows 设置里改过排布、
# 分辨率、主屏，恢复旧快照会把这些改动全部覆盖掉——这正是"程序自己维护状态"
# 的典型错误。程序的职责只是「读当前拓扑 + 把要用的屏加回当前拓扑」。
#
# 现算依据（2026-11-27 真机实测，scripts/topo-declarative-probe.py --verify）：
#   DISPLAYCONFIG_PATH_SOURCE_INFO.modeInfoIdx = (sourceModeIdx << 16) | 0xFFFF
#   DISPLAYCONFIG_PATH_TARGET_INFO.modeInfoIdx = (targetModeIdx << 16) | desktopModeIdx
#   按 (infoType, id) 在**当前** modes 数组里查，得到的三元组与 Windows 自己填的
#   值逐位一致（path#0 0x0001FFFF/0x00000002、path#1 0x0004FFFF/0x00030005）。
_CCD_TYPE_SOURCE = 1        # DISPLAYCONFIG_MODE_INFO_TYPE_SOURCE

_CCD_TYPE_TARGET = 2        # DISPLAYCONFIG_MODE_INFO_TYPE_TARGET

_CCD_TYPE_DESKTOP = 3       # DISPLAYCONFIG_MODE_INFO_TYPE_DESKTOP_IMAGE
