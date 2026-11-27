# -*- coding: utf-8 -*-
"""屏幕动作枚举（""不变 / off 熄灭 / on 点亮）。

v2.03.0 U3 前置：SlotEditor 等控件要用，下沉到 domain 避免控件反向依赖主文件。
"""

# 每个时间段可携带的屏幕动作：""（不变）/ off（到点熄灭）/ on（到点点亮）
SCREEN_ACTIONS = (("", "不变"), ("off", "熄灭"), ("on", "点亮"))


SCREEN_VALUE_BY_LABEL = {label: value for value, label in SCREEN_ACTIONS}


SCREEN_LABEL_BY_VALUE = {value: label for value, label in SCREEN_ACTIONS}

