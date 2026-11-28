# -*- coding: utf-8 -*-
"""进程内配置相关可变状态的唯一持有者（R6）。

为什么单独一层：`core.config` / `core.logging` 都要读写这四项，而架构守卫禁止
core 依赖 hw/ui/app，不能把这些状态塞进 app 层的 AppState。这里保持零项目依赖，
只提供小访问器；可变容器（caps_cache / session_events）返回**同一个对象**，
不能替换，否则 hw.ddc 里 `from core.config import _CAPS_CACHE` 的引用会失联。

⚠️ 被重新赋值的字符串/布尔量不能靠 `from core.config import ...` 的快照跟随，
调用方必须走访问器或模块 `__getattr__` 兼容层（见 docs/交接文档.md §9.14）。
"""


class ConfigState:
    """本次进程的配置相关状态；只在本模块创建一次。"""

    def __init__(self):
        self.load_error = ""
        self.caps_cache = {}
        self.session_events = []
        self.diag_on = True


STATE = ConfigState()


def get_load_error():
    return STATE.load_error


def set_load_error(text):
    STATE.load_error = text


def caps_cache():
    """返回同一 dict 对象（原地读写，禁止整体替换）。"""
    return STATE.caps_cache


def session_events():
    """返回同一 list 对象（受 core.logging._EVENT_LOCK 保护，截断到 40）。"""
    return STATE.session_events


def get_diag_on():
    return STATE.diag_on


def set_diag_on(value):
    STATE.diag_on = bool(value)
