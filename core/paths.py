# -*- coding: utf-8 -*-
"""路径与文件名（v2.03.0 U5 前置，从 monitor_brightness.py 搬出）。

零依赖的一层：只认 `sys.frozen` / 环境变量 / 文件系统，不 import 任何本项目模块。
`domain/**` 与 `ui/**` 都通过它取路径，避免各自拼。

⚠️ 常量只在 import 时算一次（`_dir_writable` 会真写一次探针文件）。
测试若要改路径，改**引用方模块**里的名字（主文件 `from core.paths import ...`
后仍可被 `mb.CONFIG_FILE = ...` 覆盖），改本模块不影响已绑定的引用。
"""
import os
import sys


def _app_dir():
    """程序所在目录：打包态 = exe 所在目录；源码运行 = 脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _dir_writable(path):
    """真的试着写一下再删。

    `os.access(W_OK)` 在 Windows 上对 Program Files 之类的目录会返回 True，
    实际写的时候才报错，所以必须真写。
    """
    probe = os.path.join(path, ".mb-write-probe")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        try:
            os.remove(probe)
        except Exception:
            pass
        return False


# v2.01.17：配置也便携化到**程序所在目录**（用户要求）。
# 便携化是软件常见做法（SO #4897525）：配置文件放程序根目录，整个目录拷走
# 设置就跟着走。只有程序目录不可写（Program Files / 只读介质）才回退 APPDATA。
_LEGACY_CONFIG_DIR = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "MonitorBrightness")


APP_DIR = _app_dir()


CONFIG_DIR = APP_DIR if _dir_writable(APP_DIR) else _LEGACY_CONFIG_DIR


CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def _pick_log_dir():
    """日志放**程序同目录**（便携、出问题整个目录发给开发者就行）。

    只有程序目录不可写才回退到 APPDATA —— 装在 Program Files、只读介质、
    网络盘上时会走到这条回退路径。配置现在也在这里（v2.01.17）。
    """
    app_dir = _app_dir()
    return app_dir if _dir_writable(app_dir) else CONFIG_DIR


# 事件日志：任务重建 / 配置恢复等旁路记账落在这里，界面只读展示。
# ⚠️ 本程序启动时不会主动熄屏：启动自检只校验计划任务定义，绝不补执行任何档位。
LOG_DIR = _pick_log_dir()


EVENTS_FILE = os.path.join(LOG_DIR, "events.log")


ERROR_FILE = os.path.join(LOG_DIR, "error.log")

