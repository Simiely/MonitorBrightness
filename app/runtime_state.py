# -*- coding: utf-8 -*-
"""app 层进程运行时状态（R6 批次 B）。

只放 app 层自己持有、且 core 不需要访问的状态；core 状态在 core/state.py，
两者不能合并 —— 架构守卫禁止 core 依赖 app。
"""


class RuntimeState:
    """进程生命周期内需要保活的对象。"""

    def __init__(self):
        self.mutex_handle = None


RUNTIME_STATE = RuntimeState()
