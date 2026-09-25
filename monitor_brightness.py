#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示器亮度定时调节 - 简单 GUI 工具 (DDC/CI) [tkinter 轻量版]

功能:
  - 图形界面选择显示器、拖动滑块即时调节亮度（硬件级 DDC/CI，非软件滤镜）
  - 设置白天/晚上两个时间点与目标亮度，一键注册到 Windows 任务计划程序
  - 定时任务由系统调度，本程序无需常驻后台
  - 支持命令行模式: MonitorBrightness.exe --set <显示器序号> <亮度>  (供任务计划调用)

界面使用 Python 内置 tkinter，打包体积小、无第三方 GUI 依赖。
"""
import sys
import os
import json
import subprocess

import tkinter as tk
from tkinter import ttk, messagebox

try:
    from monitorcontrol import get_monitors
except Exception:  # pragma: no cover
    get_monitors = None

APP_TITLE = "显示器亮度定时调节"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MonitorBrightness")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TASK_DAY = "MonitorBrightness_Day"
TASK_NIGHT = "MonitorBrightness_Night"


# ---------- 配置 ----------
def load_config():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "monitor": 0,
        "day_time": "09:00",
        "day_level": 80,
        "night_time": "20:00",
        "night_level": 30,
        "enabled": False,
    }


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:  # pragma: no cover
        messagebox.showerror("保存失败", str(e))


# ---------- 亮度控制 ----------
def list_monitors():
    if get_monitors is None:
        return []
    try:
        return get_monitors()
    except Exception:
        return []


def set_brightness(index, value):
    monitors = list_monitors()
    if not monitors:
        raise RuntimeError(
            "未检测到支持 DDC/CI 的显示器。\n请确认：\n"
            "1. 显示器 OSD 菜单已开启 DDC/CI\n"
            "2. 使用 HDMI / DP / DVI 直连（避免 KVM / USB 转接）"
        )
    if index < 0 or index >= len(monitors):
        index = 0
    with monitors[index] as m:
        m.set_luminance(int(value))


def get_brightness(index):
    monitors = list_monitors()
    if not monitors:
        return None
    if index < 0 or index >= len(monitors):
        index = 0
    with monitors[index] as m:
        return m.get_luminance()


# ---------- 任务计划 ----------
def _run(args):
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def register_tasks(exe, index, day_t, day_l, night_t, night_l):
    for name in (TASK_DAY, TASK_NIGHT):
        _run(["schtasks", "/Delete", "/TN", name, "/F"])
    _run(["schtasks", "/Create", "/TN", TASK_DAY, "/SC", "DAILY", "/ST", day_t,
          "/TR", f'"{exe}" --set {index} {day_l}', "/F"])
    _run(["schtasks", "/Create", "/TN", TASK_NIGHT, "/SC", "DAILY", "/ST", night_t,
          "/TR", f'"{exe}" --set {index} {night_l}', "/F"])


def unregister_tasks():
    for name in (TASK_DAY, TASK_NIGHT):
        _run(["schtasks", "/Delete", "/TN", name, "/F"])


# ---------- 命令行模式 (供任务计划静默调用) ----------
def cli_set(index, value):
    try:
        set_brightness(int(index), int(value))
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(str(e) + "\n")
        sys.exit(1)


# ---------- GUI (tkinter) ----------
class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.resizable(False, False)
        self.cfg = load_config()
        self.monitors = []

        # 变量
        self.mon_var = tk.StringVar()
        self.cur_var = tk.StringVar(value="当前亮度: --")
        self.slider_var = tk.IntVar(value=70)
        self.enabled_var = tk.BooleanVar(value=bool(self.cfg.get("enabled", False)))
        self.day_t_var = tk.StringVar()
        self.day_l_var = tk.IntVar(value=80)
        self.night_t_var = tk.StringVar()
        self.night_l_var = tk.IntVar(value=30)
        self.status_var = tk.StringVar(value="就绪")

        self._build()
        self.refresh_monitors()
        self._load_cfg_to_ui()

    def _build(self):
        pad = {"padx": 8, "pady": 4}

        # 显示器
        g0 = ttk.LabelFrame(self.root, text="显示器")
        g0.pack(fill="x", **pad)
        self.mon_cb = ttk.Combobox(g0, textvariable=self.mon_var, width=44, state="readonly")
        self.mon_cb.pack(side="left", padx=6, pady=6)
        self.mon_cb.bind("<<ComboboxSelected>>", lambda e: self.update_current())
        ttk.Button(g0, text="刷新", command=self.refresh_monitors).pack(side="left", padx=6, pady=6)

        # 亮度调节
        g1 = ttk.LabelFrame(self.root, text="亮度调节（即时生效）")
        g1.pack(fill="x", **pad)
        ttk.Label(g1, textvariable=self.cur_var).pack(anchor="w", padx=6, pady=(6, 0))
        row = ttk.Frame(g1)
        row.pack(fill="x", padx=6, pady=4)
        self.slider = ttk.Scale(row, from_=0, to=100, orient="horizontal",
                                variable=self.slider_var, command=self._on_slide)
        self.slider.pack(side="left", fill="x", expand=True)
        self.slider_lbl = ttk.Label(row, text="70", width=4, anchor="e")
        self.slider_lbl.pack(side="left", padx=(6, 0))
        ttk.Button(g1, text="应用到显示器", command=self.apply_now).pack(anchor="w", padx=6, pady=(0, 6))

        # 定时调节
        g2 = ttk.LabelFrame(self.root, text="定时调节")
        g2.pack(fill="x", **pad)
        self.en_chk = ttk.Checkbutton(g2, text="启用每日定时", variable=self.enabled_var,
                                      command=self._update_enable_state)
        self.en_chk.pack(anchor="w", padx=6, pady=(6, 2))

        r_day = ttk.Frame(g2)
        r_day.pack(fill="x", padx=6, pady=2)
        ttk.Label(r_day, text="白天时间").pack(side="left")
        self.day_t_entry = ttk.Entry(r_day, textvariable=self.day_t_var, width=7)
        self.day_t_entry.pack(side="left", padx=4)
        ttk.Label(r_day, text="亮度").pack(side="left", padx=(10, 0))
        self.day_l_spin = ttk.Spinbox(r_day, from_=0, to=100, textvariable=self.day_l_var, width=5)
        self.day_l_spin.pack(side="left", padx=4)

        r_night = ttk.Frame(g2)
        r_night.pack(fill="x", padx=6, pady=2)
        ttk.Label(r_night, text="晚上时间").pack(side="left")
        self.night_t_entry = ttk.Entry(r_night, textvariable=self.night_t_var, width=7)
        self.night_t_entry.pack(side="left", padx=4)
        ttk.Label(r_night, text="亮度").pack(side="left", padx=(10, 0))
        self.night_l_spin = ttk.Spinbox(r_night, from_=0, to=100, textvariable=self.night_l_var, width=5)
        self.night_l_spin.pack(side="left", padx=4)

        r_btn = ttk.Frame(g2)
        r_btn.pack(fill="x", padx=6, pady=6)
        ttk.Button(r_btn, text="保存并启用定时", command=self.save_schedule).pack(side="left", padx=2)
        ttk.Button(r_btn, text="测试白天亮度", command=lambda: self.test_level("day")).pack(side="left", padx=2)
        ttk.Button(r_btn, text="测试晚上亮度", command=lambda: self.test_level("night")).pack(side="left", padx=2)
        ttk.Button(r_btn, text="取消定时", command=self.cancel_schedule).pack(side="left", padx=2)

        ttk.Label(self.root, textvariable=self.status_var, foreground="#666").pack(anchor="w", padx=10, pady=(0, 8))

        self._schedule_widgets = [self.day_t_entry, self.day_l_spin, self.night_t_entry, self.night_l_spin]

    def _on_slide(self, _=None):
        self.slider_lbl.config(text=str(int(float(self.slider_var.get()))))

    def _load_cfg_to_ui(self):
        self.day_t_var.set(self.cfg.get("day_time", "09:00"))
        self.night_t_var.set(self.cfg.get("night_time", "20:00"))
        self.day_l_var.set(int(self.cfg.get("day_level", 80)))
        self.night_l_var.set(int(self.cfg.get("night_level", 30)))
        self._update_enable_state()

    def _update_enable_state(self):
        on = self.enabled_var.get()
        state = "normal" if on else "disabled"
        for w in self._schedule_widgets:
            try:
                w.config(state=state)
            except Exception:
                pass

    def refresh_monitors(self):
        self.monitors = list_monitors()
        if not self.monitors:
            self.mon_cb["values"] = ["未检测到 DDC/CI 显示器"]
            self.mon_var.set("未检测到 DDC/CI 显示器")
        else:
            vals = []
            for i, m in enumerate(self.monitors):
                label = getattr(m, "model", None) or str(m)
                vals.append(f"{i}: {label}")
            self.mon_cb["values"] = vals
            idx = int(self.cfg.get("monitor", 0))
            if 0 <= idx < len(vals):
                self.mon_var.set(vals[idx])
            else:
                self.mon_var.set(vals[0])
        self.update_current()

    def current_index(self):
        try:
            return int(self.mon_var.get().split(":")[0])
        except Exception:
            return 0

    def update_current(self):
        try:
            v = get_brightness(self.current_index())
            self.cur_var.set(f"当前亮度: {v}" if v is not None else "当前亮度: --")
        except Exception:
            self.cur_var.set("当前亮度: 读取失败")

    def apply_now(self):
        val = int(float(self.slider_var.get()))
        try:
            set_brightness(self.current_index(), val)
            self.status_var.set(f"已设置显示器 {self.current_index()} 亮度为 {val}")
            self.update_current()
        except Exception as e:
            messagebox.showerror("失败", str(e))
            self.status_var.set("应用失败")

    def test_level(self, which):
        val = int(self.day_l_var.get()) if which == "day" else int(self.night_l_var.get())
        try:
            set_brightness(self.current_index(), val)
            self.status_var.set(f"测试：亮度设为 {val}")
            self.update_current()
        except Exception as e:
            messagebox.showerror("失败", str(e))

    def save_schedule(self):
        if not self.enabled_var.get():
            messagebox.showinfo("提示", "请先勾选「启用每日定时」")
            return
        idx = self.current_index()
        day_t = self.day_t_var.get().strip()
        night_t = self.night_t_var.get().strip()
        day_l = int(self.day_l_var.get())
        night_l = int(self.night_l_var.get())
        self.cfg.update({"monitor": idx, "day_time": day_t, "day_level": day_l,
                         "night_time": night_t, "night_level": night_l, "enabled": True})
        save_config(self.cfg)
        try:
            register_tasks(sys.executable, idx, day_t, day_l, night_t, night_l)
            self.status_var.set(f"已启用定时：{day_t}→{day_l}% / {night_t}→{night_l}%")
            messagebox.showinfo(
                "成功",
                "定时任务已写入 Windows 任务计划程序。\n"
                "到点会自动调节亮度，本程序无需常驻后台。\n"
                "可在「任务计划程序」中查看 MonitorBrightness_Day / _Night。",
            )
        except Exception as e:
            messagebox.showerror("注册任务失败", str(e))

    def cancel_schedule(self):
        unregister_tasks()
        self.cfg["enabled"] = False
        save_config(self.cfg)
        self.enabled_var.set(False)
        self._update_enable_state()
        self.status_var.set("已取消定时任务")


def main():
    args = sys.argv[1:]
    if len(args) >= 3 and args[0] == "--set":
        cli_set(args[1], args[2])
        return
    root = tk.Tk()
    App(root)
    if get_monitors is None:
        messagebox.showerror("缺少依赖", "monitorcontrol 未能加载，请重新安装本程序。")
    root.mainloop()


if __name__ == "__main__":
    main()
