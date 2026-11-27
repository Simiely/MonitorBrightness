# -*- coding: utf-8 -*-
"""基础工具（v2.03.0 R1，从 monitor_brightness.py 搬出）。

只依赖标准库的纯小函数：控制台输出、错误输出、宽松整数解析、时间串缩写。
本层不持有可变状态，也不 import 本项目其它层；函数体逐字搬移，零行为变化。
"""
import re
import sys


def _out(msg=""):
    """打印到 stdout。--windowed 打包后若没有控制台，sys.stdout 可能是 None。"""
    try:
        if sys.stdout is not None:
            sys.stdout.write(str(msg) + "\n")
            sys.stdout.flush()
    except Exception:
        pass


def _err(msg):
    try:
        if sys.stderr is not None:
            sys.stderr.write(str(msg) + "\n")
            sys.stderr.flush()
    except Exception:
        pass


def _safe_int(text, fallback=0):
    try:
        return int(float(str(text).strip()))
    except Exception:
        return fallback


def _short_dt(text):
    """'2026-11-04 08:30' → '11-04 08:30'；格式不认识就原样返回。"""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})$", str(text or "").strip())
    return "%s-%s %s" % (m.group(2), m.group(3), m.group(4)) if m else str(text or "")
