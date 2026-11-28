# -*- coding: utf-8 -*-
"""诊断动作（v2.03.0 R4-2，从 monitor_brightness.py 逐字搬出）。"""
import os
import subprocess

from core.logging import diag_export
from core.paths import LOG_DIR


class _DiagActions:
    """诊断：打开日志目录、导出诊断包。

    v2.03.0 U5-2：从 App 里按职责切出。方法体一字未改（`self.xxx`
    仍指向最终的 App 实例），因此零行为变化。将来的物理分离只需
    把这些类搬到独立文件再 import —— 那时才算真正的模块化。
    """

    def open_log_dir(self):
        """打开日志目录（和程序同目录；不可写时才在 APPDATA）。"""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.startfile(LOG_DIR)             # noqa: S606 — 只打开自家目录
        except Exception as e:
            self.status_var.set("打开日志目录失败：%s" % e)

    def export_diag(self):
        """导出诊断包，并直接在资源管理器里选中它。"""
        path, err = diag_export()
        if err:
            self.status_var.set("导出失败：%s" % err)
            self.msg.showerror("导出诊断包失败", err)
            return
        self.status_var.set("诊断包已导出：%s" % path)
        try:
            # 路径可能含空格/逗号；用 list2cmdline 统一加引号，避免 explorer
            # 把 /select, 后面的路径拆成多个参数。
            subprocess.Popen(["explorer", "/select," + subprocess.list2cmdline([path])])
        except Exception:
            pass
