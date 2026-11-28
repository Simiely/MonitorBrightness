# -*- coding: utf-8 -*-
"""应用装配与入口编排（R4-5，从 monitor_brightness.py 逐字搬出）。"""

import sys
import os
import queue
import shutil
import threading
import traceback
import ctypes.wintypes
from ctypes import wintypes
from datetime import datetime
import tkinter as tk
from core.paths import CONFIG_DIR, ERROR_FILE, LOG_DIR
import core.config as _config
from core.config import APP_TITLE, APP_VERSION, load_config, old_config_present
from core.logging import _write_event_log, dlog
from service import current_level
from ui.theme import PIL_OK, UI, apply_dark_theme, apply_tk_scaling, apply_dark_titlebar
from ui.widgets import Button, ScrollArea
from ui.sheets import DarkBox, HelpSheet, SettingsSheet
from ui.panels import BrightnessPanel, MonitorPanel, SchedulePanel, StatusCard
from app.diag import _DiagActions
from app.settings import _SettingsActions
from app.brightness import _BrightnessActions
from app.screen_power import _ScreenPowerActions
from app.schedule import _ScheduleActions
from app.layout import _LayoutActions
from app.cli_entry import _run_cli
from app.topology_actions import _TopologyActions


def _migrate_old_logs():
    """老版本把日志写在 APPDATA，换到程序目录后把现有的几份搬一次过来。

    v2.01.5 之前日志在 `%APPDATA%\\MonitorBrightness`；现在优先放程序目录，
    这样出问题时整个目录打包发出去就行。搬一次、不覆盖已有的。
    **只在 main() 里调用**：单元测试不该有这个副作用。
    """
    if os.path.abspath(LOG_DIR) == os.path.abspath(CONFIG_DIR):
        return
    for name in ("events.log", "events.log.1", "error.log", "diag.log"):
        src = os.path.join(CONFIG_DIR, name)
        dst = os.path.join(LOG_DIR, name)
        try:
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
        except Exception:
            pass


def _raster_line(x0, y0, x1, y1, thick=2):
    """把一条线段栅格化成像素集合（用于画对勾，避免手工列像素）。"""
    pts = set()
    steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * 3))
    for i in range(steps + 1):
        t = i / float(steps)
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        for dx in range(thick):
            for dy in range(thick):
                pts.add((int(round(x)) + dx, int(round(y)) + dy))
    return pts


def _route_wheel(event):
    """把滚轮事件交给鼠标下最近的「可滚动容器」。

    为什么不再让各个容器自己 bind_all：Tk 的 `unbind_all("<MouseWheel>")`
    会清掉**所有**绑定。P-27 那次修复（Sheet 里 bind_all + Destroy 时
    unbind_all）就引入了这个副作用 —— **开一次设置页再关掉，主界面滚轮
    永久失效**（用户实测：滚轮控制不了上下滚动）。

    现在的做法：整个进程只绑一次、永不 unbind；容器在自身上挂
    `_wheel_target` 标记，由本函数沿 master 链往上找。
    容器返回 False 表示"我不需要滚"，继续往上找。
    """
    w = getattr(event, "widget", None)
    while w is not None:
        target = getattr(w, "_wheel_target", None)
        if target is not None and target(event) is not False:
            return "break"
        w = getattr(w, "master", None)
    return None


def install_wheel_router(root):
    """进程级滚轮路由：只装一次（main 里调），谁都不许 unbind_all。"""
    root.bind_all("<MouseWheel>", _route_wheel, add="+")


class App(_BrightnessActions, _ScreenPowerActions, _ScheduleActions,
          _LayoutActions, _TopologyActions,
          _SettingsActions, _DiagActions):
    """协调者：服务调用、异步编排、弹窗；界面细节都在上面的控件里。"""

    def __init__(self, root, start_minimized=False):
        """装配窗口。四段拆开是为了让每段能单独看懂（v2.03.0 U5）：
        窗口设置 → 状态初始化 → UI 装配 → 启动序列，顺序不能变。"""
        self.root = root
        self.tray = None
        self._init_window()
        self._init_state()
        self._build_ui()
        self._start(start_minimized)

    def _init_window(self):
        """窗口标题 / 尺寸策略 / 深色标题栏。"""
        self.root.title("%s v%s" % (APP_TITLE, APP_VERSION))
        self.root.resizable(False, False)
        apply_dark_theme(self.root)


    def _init_state(self):
        """配置、显示器数据、各种运行标志、状态变量。"""
        self.cfg = load_config()
        # R1：CONFIG_LOAD_ERROR 是 core.config 的模块级状态，必须读定义处的
        # 当前值；主文件里再导出的同名字符串只是 import 时的快照。
        self._config_error = _config.CONFIG_LOAD_ERROR
        self.monitors = []
        self.mon_info = []
        self.topo_inactive = []           # 拓扑模式下断开的物理屏（GUI 灰显行）
        self._row_action = False          # 本次动作来自行内逐台按钮（不改勾选）
        self._screen_states = {}          # 逐台亮灭（只有 DDC 模式需要，决定行内按钮）
        # 走查 E2-03：models 键被 _load_model_cache 统一 str() 存盘，但一个
        # 手工编辑/旧版残留的非数字键会让 int(k) 抛 ValueError，windowed 下
        # stderr=None → 用户只看到"程序打不开"。跳过坏键，不拖垮 GUI。
        self.mon_models = {}
        for k, v in self.cfg.get("models", {}).items():
            try:
                self.mon_models[int(k)] = v
            except (TypeError, ValueError):
                continue
        self._read_queue = queue.Queue()
        self._bg_tasks = 0
        self._busy = False
        self._busy_action = None      # 当前忙任务对应的屏幕动作（"on"/"off"/None）
        self._pending_screen = None   # 忙时最后一次点击，结束后补执行一次
        self._task_querying = False
        self._relight_pending = False
        self._relight_timer = None       # 走查 E1-05：自动复亮 timer 句柄（可取消）
        # 启动时**不猜**屏幕状态：unknown 直到第一次「检测」拿到真实读数。
        # v2.01.0：以前这里从配置里读 screen_state，等于把上次的记账当成当前事实。
        self._last_verdict = {"state": "unknown", "detail": ""}
        self.status_var = tk.StringVar(value="就绪")
        self.msg = DarkBox(self.root)
        # windowed 打包后 stderr 为 None，Tk 回调异常会彻底静默；
        # 落到 error.log + 状态栏，避免再出现「点了没反应」式黑洞。
        self.root.report_callback_exception = self._report_tk_error
        self._sheet = None
        self._editor = None
        self._probing = False
        self._brighting = False
        self._fit_pending = False
        self._fitted_h = -1
        self._schedule_reg_timer = None
        self._restore_after = None       # 恢复双屏倒计时 timer 句柄（可取消）
        self._restore_deadline = 0.0
        self._auto_checked = False


    def _build_ui(self):
        """滚动区、顶部入口、三个面板、状态回显与关窗钩子。"""
        self.scroll = ScrollArea(self.root, UI["bg"], on_fit=self._fit_window)
        self.scroll.pack(side="top", fill="both", expand=True)
        self.body = self.scroll.body

        # 顶部：右侧两个入口（标题由系统标题栏承担，避免重复）
        top = tk.Frame(self.body, bg=UI["bg"])
        top.pack(fill="x", padx=14, pady=(8, 0))
        Button(top, "设置", self.open_settings, kind="ghost", width=52,
               height=24, pad_bg=UI["bg"], font_size=8).pack(side="right")
        Button(top, "说明", self.open_help, kind="ghost", width=52,
               height=24, pad_bg=UI["bg"], font_size=8).pack(side="right", padx=(0, 6))

        # 显示器选择在最上面，屏幕控制（状态卡）移到其下（用户要求）
        self.mon_panel = MonitorPanel(self)
        self.status_card = StatusCard(self)
        self.status_card.pack(fill="x", padx=14, pady=(8, 0))
        self.bri_panel = BrightnessPanel(self, current_level(self.cfg),
                                         self.cfg.get("presets"))
        self.sch_panel = SchedulePanel(self, self.cfg)
        # 底部留白：最后一张卡（定时）的下缘不贴窗口下边（用户要求 10px）
        tk.Frame(self.body, bg=UI["bg"], height=10).pack(fill="x")

        # 没有底部状态栏：status_var.set() 的消息统一走状态卡的临时反馈行
        # （闪 3 秒回到常驻提示）。trace 必须在状态卡建好之后再挂。
        self.status_var.trace_add(
            "write", lambda *_: self.status_card.flash(self.status_var.get()))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)


    def _start(self, start_minimized):
        """启动序列：先让窗口画出来，再枚举/探测。

        启动优化（不升版号）：`refresh_monitors` 会同步枚举 DDC + CCD，
        原来在 mainloop 之前跑，窗口要等它做完才可见。
        现在用 after(50) 把首屏探测挪到第一帧之后，窗口先出、列表晚一点填。
        `refresh_task_status` 本来就走后台，保持在前面不占首屏。
        """
        self.root.after(50, self._start_probe)
        self.refresh_task_status()
        if self.cfg.get("tray_enabled", True):
            self._create_tray()
        if start_minimized and self.tray is not None:
            self.root.withdraw()

        # 配置读坏的显式提示（不能静默吞掉：用户的设置还在坏文件里）
        self.root.after(150, self._warn_config_error)

        # 启动后台自检：只校验任务定义与当前进程是否一致（不一致则原地重建）。
        # 不补执行、不改亮度、不熄屏；900ms 是给首屏探测让路，全程后台线程。
        self.root.after(900, self._start_auto_checks)

        # v2.01.20：发现旧 APPDATA 配置时提示一句（不自动迁移）。
        # 延迟到首屏探测之后，避免被启动期的其它状态文案盖掉。
        old_cfg = old_config_present()
        if old_cfg:
            _write_event_log("检测到旧版配置（未自动迁移）：%s" % old_cfg)
            self.root.after(1400, lambda p=old_cfg: self.status_var.set(
                "旧版配置在 %s；要沿用就手动复制 config.json 到程序目录" % p))

    def _start_probe(self):
        """首帧之后的首次枚举：先把窗口画出来，再填显示器列表。

        首次启动还要补写锚点（v2.01.17）：只在"从未保存过锚点"时写一次，
        不覆盖用户后续的手动选择；必须等 refresh_monitors 建好列表后再跑。
        """
        self.refresh_monitors()
        self.probe_screens()
        if not (self.cfg.get("monitor_anchors") or []):
            self.root.after_idle(self._first_run_detect)

    def _fit_window(self):
        """内容晚到（任务状态行/显示器行）后按请求尺寸重排窗口。

        窗口 resizable(False) 时 WM 不会自动跟随内容增长，晚到的行会被
        底边裁掉（用户实测）。两个护栏：
        1) 防抖：合并 80ms 内的多次触发（探测/任务状态/渲染常常连环触发）；
        2) 高度没变就跳过 geometry("")——整窗重排不便宜，且会闪。
        超高部分仍由 ScrollArea 的封顶 + 滚动条兜底。
        """
        if self._fit_pending:
            return
        self._fit_pending = True
        self.root.after(80, self._fit_window_now)

    def _fit_window_now(self):
        self._fit_pending = False
        try:
            self.root.update_idletasks()
            need = self.scroll.body.winfo_reqheight()
            if need == self._fitted_h:
                return
            self._fitted_h = need
            self.root.geometry("")
        except Exception:
            pass

    # ---------- 顶部入口 ----------
    def open_settings(self):
        try:
            # 必须留一个引用：Toplevel 被 GC 时窗口会跟着消失（实测踩过）
            self._sheet = SettingsSheet(self)
        except Exception as e:
            self.msg.showerror("打开设置失败", "%s: %s" % (type(e).__name__, e))

    def open_help(self):
        try:
            self._sheet = HelpSheet(self)
        except Exception as e:
            self.msg.showerror("打开说明失败", "%s: %s" % (type(e).__name__, e))
















    def selected_indices(self):
        return self.mon_panel.selected()








    # ---------- 统一异步入口 ----------
    def run_bg(self, job, on_done=None, busy=False):
        if busy:
            self._busy = True
            self._set_actions_enabled(False)
        self._bg_tasks += 1

        def runner():
            try:
                result = job()
            except Exception as e:
                result = e
            self._read_queue.put(("done", (on_done, result, busy), None))

        threading.Thread(target=runner, daemon=True).start()
        self._poll_read_queue()

    def _poll_read_queue(self):
        # 走查 E1-03：screen_action 分支直接调 screen_on/off → run_bg 内又同步调
        # _poll_read_queue，每次重入都会多挂一条 after(150)，链数随调用次数
        # 线性增长且永不回落。用 _polling 护栏保证同时只有一条链。
        if getattr(self, "_polling", False):
            return
        self._polling = True
        try:
            self._poll_read_queue_inner()
        finally:
            self._polling = False

    def _poll_read_queue_inner(self):
        while True:
            try:
                kind, payload, _extra = self._read_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "screen_action":
                (self.screen_off if payload == "off" else self.screen_on)()
                continue
            if kind != "done":
                continue
            on_done, result, busy = payload
            self._bg_tasks = max(0, self._bg_tasks - 1)
            if busy:
                self._busy = False
                self._busy_action = None
                self._set_actions_enabled(True)
            try:
                if on_done is not None:
                    on_done(result)
            finally:
                # 忙时排队的屏幕请求：当前动作一结束就补执行（只一次）
                if busy:
                    self._dispatch_pending_screen()
        if self._bg_tasks > 0 or self._relight_pending:
            self.root.after(150, self._poll_read_queue)





































    def _on_close(self):
        if self.tray is not None:
            # 倒计时中进行时最小化：不让它在用户看不到界面时偷偷恢复
            if getattr(self, "_restore_after", None) is not None:
                self.cancel_restore_default()
            self.root.withdraw()
            self.status_var.set("已最小化到托盘（右下角图标，右键可退出）")
            return
        self.root.destroy()

    # ---------- 启动自检（只校验任务，不执行业务动作） ----------
    def _report_tk_error(self, exc, val, tb):
        """Tk 回调异常兜底：windowed 下 stderr 不可见，必须落盘并提示。"""
        detail = "".join(traceback.format_exception(exc, val, tb))
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(ERROR_FILE, "a", encoding="utf-8") as f:
                f.write("%s\n%s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), detail))
        except Exception:
            pass
        short = "%s: %s" % (getattr(exc, "__name__", "错误"), val)
        try:
            _write_event_log("界面异常 %s" % short)
        except Exception:
            pass
        try:
            self.status_var.set("界面异常：%s（详见 error.log）" % short)
        except Exception:
            pass

    def _warn_config_error(self):
        if self._config_error:
            self.status_var.set("配置文件读取失败，已使用默认值")
            self.msg.showwarning("配置文件有问题", self._config_error)


_MUTEX_NAME = "MonitorBrightness_SingleInstance"


_mutex_handle = None


def acquire_single_instance(name=_MUTEX_NAME):
    """尝试获取单实例互斥体。返回 True=本进程是首个实例。

    已有实例时：尝试把它的主窗口恢复并置前，然后返回 False（调用方应退出）。
    name 参数供测试注入专用互斥体名（默认用全局单实例名）。
    互斥体随进程退出自动释放（崩溃残留的 abandoned 状态不影响下次创建）。
    """
    global _mutex_handle
    if os.name != "nt":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.LPCWSTR]
    handle = k32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        _activate_existing_window()
        if handle:
            k32.CloseHandle(handle)
        return False
    _mutex_handle = handle                      # 保引用，防止句柄被 GC 关闭
    return True


def _activate_existing_window():
    """把已运行实例的主窗口（按标题前缀枚举，含托盘隐藏态）恢复并置前。"""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith(APP_TITLE):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for hwnd in found:
        user32.ShowWindow(hwnd, 9)              # SW_RESTORE
        user32.SetForegroundWindow(hwnd)




def _start_gui(args):
    """单实例检查通过后装配 Tk；失败路径只提示，不抛异常。"""
    if not acquire_single_instance():
        return                                  # 已有实例：已唤起其窗口，直接退出
    if not PIL_OK:
        # GUI 明确报错退出；CLI 路径（--set/--apply-schedule/--diag/--selftest）不受影响
        from tkinter import messagebox
        messagebox.showerror(
            "缺少 Pillow",
            "图形界面需要 Pillow（用于圆角抗锯齿贴图），当前环境没有安装。\n\n"
            "命令行功能仍可正常使用：--set / --list / --probe / --diag / --selftest / "
            "--apply-schedule。\n安装后重试：pip install pillow")
        return
    root = tk.Tk()
    apply_tk_scaling(root)          # 必须在建控件之前：字体按新比例算
    install_wheel_router(root)      # 滚轮全局路由：只装这一次，谁都不许 unbind_all
    App(root, start_minimized=("--minimized" in args))
    apply_dark_titlebar(root)
    root.mainloop()


def main():
    args = sys.argv[1:]
    # v2.01.20：不自动迁移旧配置（用户决策：每台电脑各用各的）。
    # 旧 APPDATA 文件只在界面上提示一句，由用户决定是否手动拷贝。
    _migrate_old_logs()
    dlog("app.start", ver=APP_VERSION, argv=args,
         packaged=bool(getattr(sys, "frozen", False)),
         py="%d.%d.%d" % sys.version_info[:3])
    if _run_cli(args):
        return
    _start_gui(args)
