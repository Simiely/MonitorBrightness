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
from app.schedule import _finish_enable_register


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
        self.refresh_monitors()
        self.probe_screens()
        self.status_var.set("就绪")

    def _finish_tray_register(self, result):
        # 托盘开关与界面开关共用同一套“失败回滚 + 成功刷新”收尾。
        _finish_enable_register(self, result)

    def _tray_quit(self):
        self._stop_tray()
        self.root.destroy()
