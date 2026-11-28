"""不碰硬件的回归测试：python -m unittest discover -s tests -v

覆盖 v1.9.x 修的几处核心逻辑：
  - 配置原子写 / 坏配置备份与提示 / 熄灭方式默认 ddc
  - 时间解析与旧配置兼容
  - 启动自检只校验计划任务，绝不补执行、绝不熄屏（v1.9.4）
  - 计划任务参数拼装与落盘 XML 校验
  - 「添加时间段」点击后立即创建弹窗、跑事件循环即可见，并能在弹窗里完成一次添加
  - v1.9.2 任务重建成功：经 after(0) 创建的「确认」窗在下一轮事件可见，点确认销毁；失败/启动自愈不弹
"""
import app.base as app_base_mod
import app.cli_entry as app_cli_entry_mod
import app.brightness as app_bright_mod
import app.common as app_common_mod
import app.diag as app_diag_mod
import app.layout as app_layout_mod
import app.schedule as app_schedule_mod
import app.screen_power as app_power_mod
import app.settings as app_settings_mod
import cli as cli_mod
import contextlib
import ctypes
import glob
import html
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import unittest.mock
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.config as cfg_mod
from core import state as _core_state
import core.logging as log_mod
import domain.slots as slots_mod
import hw.ccd as hw_ccd
import hw.ccd_structs as hw_structs
import hw.ddc as hw_ddc
import hw.geometry as hw_geometry
import hw.power as hw_power
import hw.topology as hw_topology
import hw.winfollow as hw_winfollow
import monitor_brightness as mb
import schedule as schedule_mod
import schedule_report as sched_report_mod
import service as svc_mod
import ui.sheets as ui_sheets_mod

# R2：同一个函数可能同时绑在主文件与多个 hw/service 模块里。测试 patch 必须
# 覆盖**调用方所在的那个绑定**，否则 mock 根本不会被调用到。这里用同一个
# mock 实例同时 patch 所有持有该名字的模块，`as x` 拿到的仍是这个实例。
_PATCH_MODULES = (mb, cfg_mod, log_mod, slots_mod, hw_ddc, hw_structs, hw_ccd,
                  hw_topology, hw_geometry, hw_power, hw_winfollow, svc_mod,
                  schedule_mod, sched_report_mod, cli_mod, ui_sheets_mod,
                  app_settings_mod, app_diag_mod, app_common_mod,
                  app_bright_mod, app_power_mod, app_schedule_mod,
                  app_layout_mod, app_base_mod)


_UNSET = object()


class _PatchAll:
    def __init__(self, name, new, kwargs):
        self.mock = new if new is not _UNSET else unittest.mock.MagicMock(**kwargs)
        self.stack = contextlib.ExitStack()
        for mod in _PATCH_MODULES:
            if hasattr(mod, name):
                self.stack.enter_context(
                    unittest.mock.patch.object(mod, name, new=self.mock))

    def __enter__(self):
        self.stack.__enter__()
        return self.mock

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


def patch_any(name, new=_UNSET, **kwargs):
    return _PatchAll(name, new, kwargs)


def set_any(name, value):
    """直接赋值版打桩：把 value 写到所有持有 name 的模块，返回恢复记录。"""
    saved = []
    for mod in _PATCH_MODULES:
        if hasattr(mod, name):
            saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
    return saved


def restore_any(saved):
    for mod, name, old in saved:
        setattr(mod, name, old)

_MODULE_TMP = None


def setUpModule():
    """整份测试都不许写用户的真实配置 / 日志目录。

    有些用例类（TopologyPlanOnTest 等）直接继承 unittest.TestCase，不经过
    TempConfigCase；它们一旦触发 dlog / _write_event_log，就会写到
    %APPDATA%\\MonitorBrightness 去 —— 实测把单元测试的假数据写进了真实
    diag.log。这里给整份测试兜一个临时目录。
    """
    global _MODULE_TMP
    _MODULE_TMP = tempfile.mkdtemp(prefix="mb-test-module-")
    _MODULE_LEGACY = os.path.join(_MODULE_TMP, "legacy")
    # R1：配置/日志函数已沉到 core/，必须把路径同时指向临时目录，
    # 否则它们读的是自己模块里 import 时的真实 APPDATA 路径。
    mb.CONFIG_DIR = cfg_mod.CONFIG_DIR = _MODULE_TMP
    mb.CONFIG_FILE = cfg_mod.CONFIG_FILE = os.path.join(_MODULE_TMP, "config.json")
    mb.LOG_DIR = log_mod.LOG_DIR = _MODULE_TMP
    mb.EVENTS_FILE = log_mod.EVENTS_FILE = os.path.join(_MODULE_TMP, "events.log")
    # v2.01.20：save_config 会把配置镜像到 APPDATA，模块级也必须隔离，
    # 否则任何没继承 TempConfigCase 的用例都会写进用户真实配置。
    mb._LEGACY_CONFIG_DIR = cfg_mod._LEGACY_CONFIG_DIR = _MODULE_LEGACY


def tearDownModule():
    shutil.rmtree(_MODULE_TMP, ignore_errors=True)


def pump_ui(root, rounds=2):
    """跑固定轮数的 Tk 事件循环，把 UI 回归用例从墙钟时间上摘下来。

    实测（2026-11-27）：两个回归场景的弹窗都不需要等待真实时间——
      1. `SchedulePanel.add_row` 在点击调用内**同步**创建 Toplevel，1 次 update 即 mapped；
      2. `_announce_rebuilt` 用 `root.after(0, ...)` 投递通知，下一轮 update 即 mapped。
    所以旧写法 `time.time() + 0.3` 只是给慢机器留余量，却把「跑得快」写成了
    「必须 300ms 内」——在 2 核 CI runner 上（Tk 初始化实测 810ms）就是 flake 源。
    """
    for _ in range(rounds):
        root.update()


class TempConfigCase(unittest.TestCase):
    """把 CONFIG_DIR / CONFIG_FILE / EVENTS_FILE 指到临时目录。

    v2.01.20：`_LEGACY_CONFIG_DIR` 也必须一起指走 —— `save_config` 现在会
    镜像一份配置到 APPDATA 当迁移种子，不隔离的话单测会覆盖**用户真实配置**
    （走查时自查发现，属 P-24）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mb-test-")
        self._old = (mb.CONFIG_DIR, mb.CONFIG_FILE, mb.EVENTS_FILE,
                     mb.LOG_DIR, mb._LEGACY_CONFIG_DIR,
                     cfg_mod.CONFIG_DIR, cfg_mod.CONFIG_FILE,
                     cfg_mod._LEGACY_CONFIG_DIR,
                     log_mod.LOG_DIR, log_mod.EVENTS_FILE)
        # R6：会被重新赋值的状态走访问器，旧模块名只做只读兼容。
        self._old_load_error = _core_state.get_load_error()
        # R1：主文件与 core 模块各有一份 import 时绑定的路径常量，必须同步改。
        mb.CONFIG_DIR = cfg_mod.CONFIG_DIR = self.tmp
        mb.CONFIG_FILE = cfg_mod.CONFIG_FILE = os.path.join(self.tmp, "config.json")
        mb.LOG_DIR = log_mod.LOG_DIR = self.tmp
        mb.EVENTS_FILE = log_mod.EVENTS_FILE = os.path.join(self.tmp, "events.log")
        _core_state.set_load_error("")
        mb._LEGACY_CONFIG_DIR = cfg_mod._LEGACY_CONFIG_DIR = os.path.join(self.tmp, "legacy")

    def tearDown(self):
        (mb.CONFIG_DIR, mb.CONFIG_FILE, mb.EVENTS_FILE,
         mb.LOG_DIR, mb._LEGACY_CONFIG_DIR,
         cfg_mod.CONFIG_DIR, cfg_mod.CONFIG_FILE,
         cfg_mod._LEGACY_CONFIG_DIR,
         log_mod.LOG_DIR, log_mod.EVENTS_FILE) = self._old
        _core_state.set_load_error(self._old_load_error)
        shutil.rmtree(self.tmp, ignore_errors=True)

def _row_button_texts(row):
    """递归取一行里「熄灭 / 显示」按钮的文字，按出现顺序。

    按钮包在一个 box 里，不是行的直接子控件 —— 别按下标猜。
    """
    out, queue = [], list(row.winfo_children())
    while queue:
        w = queue.pop(0)
        if getattr(w, "_text", None) in ("熄灭", "显示"):
            out.append(w._text)
        queue.extend(w.winfo_children())
    return out

class _FakeU32:
    """够用的 user32 替身：枚举窗口 + 读/写矩形。"""

    def __init__(self, rects):
        self._r = dict(rects)          # hwnd -> (x, y, w, h)
        self.moved = []                # [(hwnd, x, y)]
        self.pid = 999999              # 别等于本进程，否则会被过滤掉

    def EnumWindows(self, cb, _lp):
        for h in list(self._r):
            cb(h, 0)
        return 1

    def IsWindowVisible(self, h):
        return True

    def IsIconic(self, h):
        return False

    def GetWindowTextLengthW(self, h):
        return 5

    def GetWindowLongW(self, h, _i):
        return 0

    def GetWindowThreadProcessId(self, h, ref):
        ref._obj.value = self.pid
        return 1

    def GetWindowRect(self, h, ref):
        x, y, w, hh = self._r[h]
        r = ref._obj
        r.left, r.top, r.right, r.bottom = x, y, x + w, y + hh
        return 1

    def IsWindow(self, h):
        return h in self._r

    def SetWindowPos(self, h, _after, x, y, _cx, _cy, _flags):
        ox, oy, w, hh = self._r[h]
        self._r[h] = (x, y, w, hh)
        self.moved.append((h, x, y))
        return 1

class _TopoFixture(TempConfigCase):
    """拓扑动作层测试的共用夹具。

    单独抽出来当基类（不含 test_*），是为了让下面两个测试类共享同一份
    假拓扑而**不重复执行**彼此的用例。
    """

    ROWS_RAW = [
        {"pathIndex": 0, "index": 0, "anchor": "A1", "active": True, "connected": True,
         "gdi": "\\\\.\\DISPLAY1", "friendly": "P1", "vdd": False,
         "srcId": 0, "tgtId": 100},
        {"pathIndex": 1, "index": 1, "anchor": "A2", "active": True, "connected": True,
         "gdi": "\\\\.\\DISPLAY2", "friendly": "P2", "vdd": False,
         "srcId": 1, "tgtId": 200},
        {"pathIndex": 2, "index": 2, "anchor": "V1", "active": False, "connected": True,
         "gdi": "\\\\.\\DISPLAY92", "friendly": "VDD by MTT", "vdd": True,
         "srcId": 2, "tgtId": 300},
    ]
    TARGETS = [{"index": 0, "dev": "DISPLAY1", "primary": True},
               {"index": 1, "dev": "DISPLAY2", "primary": False}]

    def _cfg(self, **kw):
        cfg = mb.default_config()
        cfg.update(kw)
        return cfg

    @staticmethod
    def _modes():
        """造 modes 数组：source/target/desktop 各两条，id 与 ROWS_RAW 对齐。

        索引 → (infoType, id)：0→(1,0) 1→(2,100) 2→(3,100)
                              3→(1,1) 4→(2,200) 5→(3,200)
        """
        modes = (mb._CcdModeInfo * 6)()
        for i, (info_type, ident) in enumerate(
                [(1, 0), (2, 100), (3, 100), (1, 1), (2, 200), (3, 200)]):
            modes[i].infoType = info_type
            modes[i].id = ident
        return modes

    def _raw(self):
        paths = (mb._CcdPathInfo * 3)()
        modes = self._modes()
        paths[0].flags = mb._CCD_PATH_ACTIVE
        paths[1].flags = mb._CCD_PATH_ACTIVE
        for p in paths:
            p.targetInfo.targetAvailable = 1
        return (paths, modes, 3, 6, [dict(r) for r in self.ROWS_RAW]), paths

    def _off_raw(self):
        """断开后的拓扑：只剩主屏 ACTIVE，A2 有一条 source=1 空闲的 path。"""
        paths = (mb._CcdPathInfo * 4)()
        modes = self._modes()
        rows = [dict(r) for r in self.ROWS_RAW]
        rows[1].update(active=False, gdi="")
        rows.append({"pathIndex": 3, "index": -1, "anchor": "A2", "active": False,
                     "connected": True, "gdi": "", "friendly": "P2", "vdd": False,
                     "srcId": 1, "tgtId": 200})
        paths[0].flags = mb._CCD_PATH_ACTIVE
        for p in paths:
            p.targetInfo.targetAvailable = 1
        return paths, modes, 4, 6, rows

    def _base_patches(self, fake_query, apply_rc=0, verify=True):
        return (
            patch_any("_ccd_api", return_value=object()),
            patch_any("_ccd_query_raw", side_effect=fake_query),
            patch_any("_ccd_apply", return_value=apply_rc),
            patch_any("_ccd_wait_state", return_value=verify),
            patch_any("enumerate_targets",
                                       return_value=list(self.TARGETS)),
            patch_any("release_targets"),
        )
