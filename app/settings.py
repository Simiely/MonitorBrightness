# -*- coding: utf-8 -*-
"""设置写回与托盘控制器（v2.03.0 R4-2，从 monitor_brightness.py 逐字搬出）。

缺 pystray 时 GUI 照常工作，仅没有托盘/最小化到托盘能力。
"""
import threading

try:
    import pystray
    from pystray import Icon as _TrayIcon, Menu as _TrayMenu, MenuItem as _TrayItem
    TRAY_OK = True
except Exception:
    _TrayIcon = _TrayMenu = _TrayItem = None
    TRAY_OK = False

from core.config import APP_TITLE, coerce_setting, save_config, set_autostart
from core.logging import dlog, diag_set_enabled
from domain.timeutil import parse_hhmm
from schedule import (
    _days_by_min_from_fp, _next_run_text, _slot_trigger_fingerprint, task_target,
)


def tray_tooltip_text(cfg, last_verdict, title):
    """托盘悬停文案：定时摘要 + 下次运行 + 屏幕状态（最多 120 字）。"""
    if not cfg.get("enabled"):
        return "%s\n定时未启用" % title
    slots = cfg.get("slots", [])
    brief = "  ".join("%s→%d%%%s" % (x.get("time"), x.get("level", 0),
                                      "熄" if x.get("screen") == "off" else
                                      "亮" if x.get("screen") == "on" else "")
                       for x in slots)
    nxt = _next_run_text([parse_hhmm(x.get("time")) for x in slots],
                         days_by_min=_days_by_min_from_fp(
                             _slot_trigger_fingerprint(slots)))
    # v2.01.0：用**上次实际探测**的结果，不再读配置里的记账；
    # 没探测过就老实说"未检测"，不猜。
    st = {"on": "亮着", "off": "已熄灭"}.get(
        (last_verdict or {}).get("state"), "未检测")
    return ("%s\n定时: %s\n下次 %s\n屏幕: %s" % (title, brief, nxt, st))[:120]


class _SettingsActions:
    """设置与托盘：设置项写回、开机自启、托盘菜单。

    v2.03.0 U5-2：从 App 里按职责切出。方法体一字未改（`self.xxx`
    仍指向最终的 App 实例），因此零行为变化。将来的物理分离只需
    把这些类搬到独立文件再 import —— 那时才算真正的模块化。
    """

    # ---------- 设置项写回 ----------
    def apply_setting(self, key, value):
        """设置弹层里每个开关/输入框的统一出口：落配置并立即生效。"""
        try:
            if key == "tray_enabled":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
                if value:
                    self._create_tray()
                    self.status_var.set("托盘图标已启用（点 × 进托盘）")
                else:
                    self._stop_tray()
                    self.status_var.set("托盘图标已关闭（点 × 直接退出）")
            elif key == "screen_auto_relight_min":
                self.cfg[key] = max(0, min(120, int(value)))
                save_config(self.cfg)
            elif key == "wake_switch_input":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
            elif key == "diag_log":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
                # 先写一条再改开关：否则"已关闭"这一条自己就记不下来了
                dlog("app.setting", key=key, value=bool(value))
                diag_set_enabled(self.cfg[key])
                self.status_var.set("诊断日志已" + ("开启" if value else "关闭"))
            elif key == "window_follow":
                self.cfg[key] = bool(value)
                save_config(self.cfg)
                self.status_var.set("改显示器时" + ("会" if value else "不会")
                                    + "把窗口搬回原屏")
            elif key == "presets":
                self.cfg[key] = coerce_setting("presets", value)
                save_config(self.cfg)
                self.bri_panel.set_presets(self.cfg[key])
        except Exception as e:
            self.msg.showerror("保存设置失败", "%s: %s" % (type(e).__name__, e))

    def apply_autostart(self, enable):
        exe, prefix = task_target()
        cmd = ('"%s" %s--minimized' % (exe, prefix)).strip()
        try:
            set_autostart(bool(enable), cmd)
            self.status_var.set("开机自启动已" + ("开启" if enable else "关闭"))
        except Exception as e:
            self.msg.showerror("设置失败", "%s: %s" % (type(e).__name__, e))

    def apply_off_method(self, value):
        if value == "ddc" and not self.cfg.get("ddc_allow_undeclared"):
            if not self.msg.askyesno(
                    "单屏待机：有风险",
                    "这条路的「熄灭」用面板待机命令（0xD6=2），但：\n"
                    "• 你的面板能力串只声明 01/04/05，没声明 02；\n"
                    "• 微软文档警告盲写未实现的 MCCS 值会导致未定义行为；\n"
                    "• 万一唤不回，只能按显示器电源键。\n\n"
                    "点亮时会依次尝试：电源模式 → 写亮度 → 输出重驱动 → 切信号源。"):
                return
            self.cfg["ddc_allow_undeclared"] = True
        if value == "topology" and not self.msg.askyesno(
                "拓扑断开：先确认",
                "这条路会把「选中的副屏」从 Windows 桌面拓扑里移除，供串流隐藏物理屏用：\n"
                "• 主屏不会被断（Windows 实测 rc=87），虚拟屏 VDD 永不作目标；\n"
                "• 每次只断一块；恢复时先回读 ACTIVE 再写亮度（≤2 秒）；\n"
                "• 窗口位置不保证完全还原，首次使用请做一次串流回归。\n\n"
                "默认仍是「单屏待机」，选它才会切到本方式。"):
            return
        self.cfg["screen_off_method"] = value
        # v2.01.0：`ddc_allow_undeclared` 是"这一次明知有风险"的授权，不是永久状态。
        # 离开 ddc 就收回——否则用户在旧屏上点过一次「同意」，换屏后盲写
        # 未声明的待机值这件事会被悄悄继承下来（走查 E2-05 的单向阀门）。
        if value != "ddc":
            self.cfg["ddc_allow_undeclared"] = False
        save_config(self.cfg)
        if value == "ddc":
            self.status_var.set("熄灭方式：单屏待机（键鼠不会点亮）")
        elif value == "topology":
            self.status_var.set("熄灭方式：拓扑断开（副屏移除，主屏不动）")
        else:
            self.status_var.set("熄灭方式：系统级（整组熄灭，一定能唤回）")
        # v2.01.22（走查 P-29）：切模式必须重建列表 ——
        # topology 才列"断开的物理屏"，ddc/system 只留 VDD。
        # 原来只 probe_screens，用户切到 topology 后看不到能接回的屏。
        self.refresh_monitors(silent=True)
        # 状态卡的结论形态随模式变化（DDC 下不显示亮灭），立即重探一次
        self.probe_screens()

    def _stop_tray(self):
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
            self.tray = None

    # ---------- 托盘 ----------
    def _tray_image(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(30, 30, 30, 255),
                  outline=(74, 158, 255, 255), width=3)
        d.rounded_rectangle((14, 28, 50, 36), radius=4, fill=(74, 158, 255, 255))
        d.ellipse((36, 23, 52, 41), fill=(255, 255, 255, 255))
        return img

    def _tray_tooltip_text(self):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return tray_tooltip_text(self.cfg, self._last_verdict, APP_TITLE)

    def _tray_update_tooltip(self):
        if self.tray is not None:
            try:
                self.tray.tooltip = self._tray_tooltip_text()
                self.tray.update_menu()
            except Exception:
                pass

    def _create_tray(self):
        if not TRAY_OK or self.tray is not None or not self.root.winfo_exists():
            return
        menu = _TrayMenu(
            _TrayItem("打开主窗口", lambda *_: self.root.after(0, self._tray_show_main),
                      default=True),
            _TrayItem("熄灭显示器", lambda *_: self.root.after(0, self.screen_off)),
            _TrayItem("点亮显示器", lambda *_: self.root.after(0, self.screen_on)),
            _TrayItem("启用定时", lambda *_: self.root.after(0, self._tray_toggle_schedule),
                      checked=lambda _item: bool(self.cfg.get("enabled"))),
            _TrayItem("退出", lambda *_: self.root.after(0, self._tray_quit)),
        )
        self.tray = _TrayIcon("MonitorBrightness", self._tray_image(),
                              self._tray_tooltip_text(), menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _tray_show_main(self):
        self.root.deiconify()
        self.root.lift()
        # v2.01.22（走查 P-30）：窗口在托盘里期间可能拔插过屏、开关过串流，
        # 打开时必须重读，否则列表还是最小化前那一份（用户报"打开没有
        # 刷新出虚拟显示器"的另一条路径）。
        self.refresh_monitors(silent=True)
        self.probe_screens()
        self.status_var.set("就绪")

    def _finish_tray_register(self, result):
        if isinstance(result, Exception):
            result = (False, str(result))
        ok, out = result
        if not ok:
            self._rollback_schedule(False, "注册任务失败", out)
            return
        self.refresh_task_status()
        self.status_var.set("定时已启用")

    def _tray_quit(self):
        self._stop_tray()
        self.root.destroy()
