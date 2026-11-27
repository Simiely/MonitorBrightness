# -*- coding: utf-8 -*-
"""界面/调度共用的纯决策函数（v2.03.0 从 monitor_brightness.py 逐字搬出）。

**行为零变化**：这些函数原来就在主文件里，只是搬了个位置。
搬家的目的是让它们能脱离 Tk 和 Win32 单独测试。
"""


def short_dev(name):
    """'\\\\.\\DISPLAY1' / 'DISPLAY1' -> 'display1'（大小写、前缀都归一）。"""
    return str(name or "").split("\\")[-1].strip().lower()


# 兼容主文件里的老名字（那边还有 19 处引用）
_short_dev = short_dev


def is_vdd_index(index, monitors, vdd_devs):
    """这一行是不是虚拟显示器（归串流软件管，不给逐台按钮）。"""
    if index < 0 or index >= len(monitors or []):
        return False
    dev = short_dev(monitors[index].get("dev"))
    return bool(vdd_devs and dev in vdd_devs)


def ccd_friendly_name(index, monitors, ccd_names):
    """这块屏在 CCD 里的友好名，查不到返回空串。

    DDC 枚举出来的 `desc` 恒为 "Generic PnP Monitor"（真机实测），拿它当名字
    会让 VDD 显示成物理屏的通用名。CCD 的 `monitorFriendlyDeviceName` 才是
    用户看到的 "VDD by MTT" / "R27qe Gen2"。
    """
    if index < 0 or index >= len(monitors or []):
        return ""
    dev = short_dev(monitors[index].get("dev"))
    return (ccd_names or {}).get(dev) or ""


def restore_selection(prev, count):
    """刷新后界面上要勾哪些屏。

    **启动时全选**（v2.01.10 起）：用户打开程序最常见的目的就是"把所有屏
    一起调一下"。刷新时（`prev` 非空）保留用户当前的勾选，不打断他。
    定时任务的目标是另一套解析（`resolve_monitors` 读 monitor_anchors），
    不受这里影响。
    """
    keep = [i for i in (prev or []) if 0 <= i < count]
    if keep:
        return keep
    return list(range(count))


def plan_model_updates(models, current, count):
    """型号缓存里**需要刷到界面上**的行：[(index, name)]。

    跳过：读型号抛出的异常、空名、与现有相同、下标越界。
    v2.03.0 P2：原逻辑混在 `_apply_models` 里，UI 刷新和判断缠在一起。
    """
    if not isinstance(models, dict):
        return []
    out = []
    for i, name in models.items():
        try:
            idx = int(i)
        except Exception:
            continue
        if not name or idx < 0 or idx >= count or current.get(idx) == name:
            continue
        out.append((idx, name))
    return sorted(out)


# ---- v2.03.0 U4：显示名判定（MonitorPanel 要用）----
_GENERIC_DESC = "generic pnp monitor"


def is_vdd_row(row):
    """这一行是不是虚拟显示器 —— 名称显示与逐台按钮都按它判定。

    单独抽成纯函数是为了单测：VDD 当主屏时它是 index 0，绝不能套用
    mon_models[0] 那块物理屏的型号名（v2.01.12 修）。
    """
    return bool(row and row.get("vdd"))


def display_name_for_row(index, row, mon_models):
    """界面行的显示名：VDD 用 friendly，其余才用按 index 缓存的型号名。

    v2.01.21：DDC 读型号走 EDID，真机实测两块屏要 6.2 秒。缓存还没到位时
    先用 CCD 的 friendly（"R27qe Gen2"，0.1ms 就能拿到）顶上，
    别让用户盯着"显示器 0"等 6 秒。

    v2.01.22（走查 P-28）：DDC 的 `desc` 恒为 "Generic PnP Monitor"，
    它是驱动占位名、不是名字，别拿它当兜底显示。
    """
    if is_vdd_row(row):
        name = (row.get("friendly") or "").strip()
        if name.lower() == _GENERIC_DESC:
            name = ""
        return (name or "虚拟显示器"), True
    model = mon_models.get(index)
    if model:
        return model, False
    name = (row.get("friendly") or "").strip()
    if name.lower() == _GENERIC_DESC:
        name = ""
    return (name or "显示器 %d" % index), False

