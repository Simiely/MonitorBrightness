#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显示器亮度定时调节 - 简单 GUI 工具 (DDC/CI) [PySide6]

功能:
  - 图形界面选择显示器、拖动滑块即时调节亮度（硬件级 DDC/CI，非软件滤镜）
  - 设置白天/晚上两个时间点与目标亮度，一键注册到 Windows 任务计划程序
  - 定时任务由系统调度，本程序无需常驻后台
  - 支持命令行模式: MonitorBrightness.exe --set <显示器序号> <亮度>  (供任务计划调用)
"""
import sys
import os
import json
import subprocess

try:
    from monitorcontrol import get_monitors
except Exception:  # pragma: no cover
    get_monitors = None

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QSlider, QPushButton, QSpinBox, QLineEdit,
    QCheckBox, QGroupBox, QMessageBox,
)
from PySide6.QtCore import Qt

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
        QMessageBox.critical(None, "保存失败", str(e))


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


# ---------- GUI (PySide6) ----------
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.cfg = load_config()
        self.monitors = []
        self._build()
        self.refresh_monitors()
        self._load_cfg_to_ui()

    def _build(self):
        root = QVBoxLayout(self)

        g0 = QGroupBox("显示器")
        h0 = QHBoxLayout(g0)
        self.mon_cb = QComboBox()
        self.mon_cb.setMinimumWidth(360)
        h0.addWidget(self.mon_cb)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_monitors)
        h0.addWidget(btn_refresh)
        root.addWidget(g0)

        g1 = QGroupBox("亮度调节（即时生效）")
        v1 = QVBoxLayout(g1)
        self.cur_label = QLabel("当前亮度: --")
        v1.addWidget(self.cur_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(70)
        v1.addWidget(self.slider)
        btn_apply = QPushButton("应用到显示器")
        btn_apply.clicked.connect(self.apply_now)
        v1.addWidget(btn_apply)
        root.addWidget(g1)

        g2 = QGroupBox("定时调节")
        v2 = QVBoxLayout(g2)
        self.en_chk = QCheckBox("启用每日定时")
        self.en_chk.setChecked(bool(self.cfg.get("enabled", False)))
        self.en_chk.toggled.connect(self._update_enable_state)
        v2.addWidget(self.en_chk)

        row_day = QHBoxLayout()
        row_day.addWidget(QLabel("白天时间"))
        self.day_t = QLineEdit("09:00")
        self.day_t.setFixedWidth(70)
        row_day.addWidget(self.day_t)
        row_day.addWidget(QLabel("亮度"))
        self.day_l = QSpinBox()
        self.day_l.setRange(0, 100)
        self.day_l.setValue(80)
        row_day.addWidget(self.day_l)
        v2.addLayout(row_day)

        row_night = QHBoxLayout()
        row_night.addWidget(QLabel("晚上时间"))
        self.night_t = QLineEdit("20:00")
        self.night_t.setFixedWidth(70)
        row_night.addWidget(self.night_t)
        row_night.addWidget(QLabel("亮度"))
        self.night_l = QSpinBox()
        self.night_l.setRange(0, 100)
        self.night_l.setValue(30)
        row_night.addWidget(self.night_l)
        v2.addLayout(row_night)

        btn_row = QHBoxLayout()
        b_save = QPushButton("保存并启用定时")
        b_save.clicked.connect(self.save_schedule)
        btn_row.addWidget(b_save)
        b_test_d = QPushButton("测试白天亮度")
        b_test_d.clicked.connect(lambda: self.test_level("day"))
        btn_row.addWidget(b_test_d)
        b_test_n = QPushButton("测试晚上亮度")
        b_test_n.clicked.connect(lambda: self.test_level("night"))
        btn_row.addWidget(b_test_n)
        b_cancel = QPushButton("取消定时")
        b_cancel.clicked.connect(self.cancel_schedule)
        btn_row.addWidget(b_cancel)
        v2.addLayout(btn_row)

        self.status = QLabel("就绪")
        v2.addWidget(self.status)
        root.addWidget(g2)

    def _load_cfg_to_ui(self):
        self.day_t.setText(self.cfg.get("day_time", "09:00"))
        self.night_t.setText(self.cfg.get("night_time", "20:00"))
        self.day_l.setValue(int(self.cfg.get("day_level", 80)))
        self.night_l.setValue(int(self.cfg.get("night_level", 30)))
        self._update_enable_state()

    def _update_enable_state(self):
        on = self.en_chk.isChecked()
        for w in (self.day_t, self.day_l, self.night_t, self.night_l):
            w.setEnabled(on)

    def refresh_monitors(self):
        self.monitors = list_monitors()
        self.mon_cb.clear()
        if not self.monitors:
            self.mon_cb.addItem("未检测到 DDC/CI 显示器")
        else:
            for i, m in enumerate(self.monitors):
                label = getattr(m, "model", None) or str(m)
                self.mon_cb.addItem(f"{i}: {label}")
        idx = int(self.cfg.get("monitor", 0))
        if 0 <= idx < self.mon_cb.count():
            self.mon_cb.setCurrentIndex(idx)
        self.update_current()

    def current_index(self):
        try:
            return int(self.mon_cb.currentText().split(":")[0])
        except Exception:
            return 0

    def update_current(self):
        try:
            v = get_brightness(self.current_index())
            self.cur_label.setText(f"当前亮度: {v}" if v is not None else "当前亮度: --")
        except Exception:
            self.cur_label.setText("当前亮度: 读取失败")

    def apply_now(self):
        val = self.slider.value()
        try:
            set_brightness(self.current_index(), val)
            self.status.setText(f"已设置显示器 {self.current_index()} 亮度为 {val}")
            self.update_current()
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))
            self.status.setText("应用失败")

    def test_level(self, which):
        val = self.day_l.value() if which == "day" else self.night_l.value()
        try:
            set_brightness(self.current_index(), val)
            self.status.setText(f"测试：亮度设为 {val}")
            self.update_current()
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))

    def save_schedule(self):
        if not self.en_chk.isChecked():
            QMessageBox.information(self, "提示", "请先勾选「启用每日定时」")
            return
        idx = self.current_index()
        day_t = self.day_t.text().strip()
        night_t = self.night_t.text().strip()
        day_l = self.day_l.value()
        night_l = self.night_l.value()
        self.cfg.update({"monitor": idx, "day_time": day_t, "day_level": day_l,
                         "night_time": night_t, "night_level": night_l, "enabled": True})
        save_config(self.cfg)
        try:
            register_tasks(sys.executable, idx, day_t, day_l, night_t, night_l)
            self.status.setText(f"已启用定时：{day_t}→{day_l}% / {night_t}→{night_l}%")
            QMessageBox.information(
                self, "成功",
                "定时任务已写入 Windows 任务计划程序。\n"
                "到点会自动调节亮度，本程序无需常驻后台。\n"
                "可在「任务计划程序」中查看 MonitorBrightness_Day / _Night。",
            )
        except Exception as e:
            QMessageBox.critical(self, "注册任务失败", str(e))

    def cancel_schedule(self):
        unregister_tasks()
        self.cfg["enabled"] = False
        save_config(self.cfg)
        self.en_chk.setChecked(False)
        self._update_enable_state()
        self.status.setText("已取消定时任务")


def main():
    args = sys.argv[1:]
    if len(args) >= 3 and args[0] == "--set":
        cli_set(args[1], args[2])
        return
    if get_monitors is None:
        QMessageBox.critical(None, "缺少依赖", "monitorcontrol 未能加载，请重新安装本程序。")
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
