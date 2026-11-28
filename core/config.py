# -*- coding: utf-8 -*-
"""配置读写与设置项声明表（v2.03.0 R1，从 monitor_brightness.py 搬出）。

函数体逐字搬移，零行为变化。主文件用 `from core.config import ...` 再导出，
因此主文件内部的 18 处 save_config 调用与界面对 SETTINGS 的引用都不用改。

依赖方向：config → core.paths / core.logging / domain.timeutil；
diag_export 需要的 load_config 在 core.logging 里用**局部 import**取，
两模块之间没有模块级环。
"""
import json
import os
import shutil
import tempfile
from datetime import datetime

from core import state as _state
from core.logging import _write_event_log, diag_set_enabled
from core.paths import CONFIG_DIR, CONFIG_FILE, _LEGACY_CONFIG_DIR
from domain.timeutil import normalize_days, normalize_time_input, parse_hhmm

APP_TITLE = "显示器亮度定时调节"
APP_VERSION = "2.03.1"     # 滚轮改进程级路由（修 unbind_all 清空全局绑定的回归）

# ============================================================
# 一、配置读写（设置项的唯一声明表在此，其余全部由它派生）
# ============================================================
# 为什么要有这张表：实测这些键在源码里触点 5~9 处（Fowler 的 Shotgun Surgery），
# 加一个设置要同时改 默认值表 / 校验 / 建控件 / 读表单 / 拼注册脚本 五六处。
# 现在只有这里声明一次，其余地方一律派生。
#   kind: indices 显示器序号列表 | hhmm 时间 | level 亮度 0-100 | flag 开关
DEFAULT_SLOTS = [{'time': '08:30', 'level': 80, 'screen': '', 'days': []},
                 {'time': '21:00', 'level': 0, 'screen': '', 'days': []}]


# v2.04：熄灭方式固定为 topology（CCD 断开副屏）。ddc 待机与系统级广播
# 因兼容性/适用性差已移除；旧配置里的 screen_off_method / ddc_allow_undeclared /
# wake_* 键会被 load_config 自动丢弃，不影响行为。
SETTINGS = (
    {"key": "monitors", "kind": "indices", "default": []},
    # 设备名锚点（如 ['DISPLAY1','DISPLAY2']），与 monitors 同序对应。
    # 为什么不只用下标：EnumDisplayMonitors 的顺序会随显卡驱动/接线变化而重排，
    # 只存下标就会出现"熄的是这块屏、点的是那块屏"（另一台电脑点亮失效的形态之一）。
    {"key": "monitor_ids", "kind": "ids", "default": []},
    # 快捷预设：三个按钮的亮度值，用户可改（0/75/100 是出厂默认）
    {"key": "presets", "kind": "levels", "default": [0, 75, 100]},
    {"key": "slots", "kind": "slots", "default": DEFAULT_SLOTS},
    {"key": "enabled", "kind": "flag", "default": False},
    {"key": "catch_up_on_logon", "kind": "flag", "default": True},
    {"key": "screen_auto_relight_min", "kind": "level", "default": 0},    # 熄灭后 N 分钟自动点亮（0=关）
    {"key": "tray_enabled", "kind": "flag", "default": True},             # 托盘开关（关 = 点 X 直接退出）
    # ⚠️ v2.01.0 删除了 screen_state / screen_state_at / screen_off_tick：
    # 程序不再维护"屏幕现在亮还是灭"的状态账。拓扑才是唯一事实源，
    # 每次需要结论时现场读（见 screen_verdict）。旧配置里残留的同名字段
    # 会被 load_config 丢弃，不影响任何行为。
    # v2.04：熄灭只走拓扑。DDC 仅保留亮度读写与只读探测；不再写 0xD6/0x60，
    # 也不发系统级 SC_MONITORPOWER 广播。点亮 = 拓扑接回 + 写亮度。
    # 拓扑模式的显示器锚点（显卡 LUID + EDID mfr/prod + connectorInstance）。
    # E1 只读层先声明；E2 才做旧 monitors/monitor_ids 的迁移。
    {"key": "monitor_anchors", "kind": "anchors", "default": []},
    # 每块屏"断开那一刻"的桌面位置与尺寸，只为接回时摆回原位。
    # **这不是状态账**：它记的是摆放偏好，而且每次断开前都按当下的真实值
    # 重写一遍 —— 用户在 Windows 里挪过屏，这里就跟着变，不会与现实脱节。
    # 断开之后这个信息就再也读不到了（Windows 会把该屏的 mode 项摘掉），
    # 所以只能在断开前抓一次。
    {"key": "monitor_positions", "kind": "positions", "default": {}},
    # 「恢复双屏」按钮的倒计时秒数（v2.01.12）：点按钮后等这么多秒再把默认
    # 双屏布局写回，给用户一个反悔（取消）的窗口。0 = 立即恢复。
    {"key": "restore_delay", "kind": "int", "default": 10, "min": 0, "max": 120},
    # 诊断日志开关（临时项，v2.01.4 引入；问题收敛后连同 dlog 一起删掉）。
    {"key": "diag_log", "kind": "flag", "default": True},
    # 交接主屏会把所有屏平移，Windows 会顺手把应用窗口挤到一起且不搬回来。
    # 打开时由本程序在动作后把窗口"跟着它的屏"搬一次（v2.01.9）。
    {"key": "window_follow", "kind": "flag", "default": True},
)
SETTING_BY_KEY = {s["key"]: s for s in SETTINGS}
CONFIG_VERSION = 7


def coerce_setting(key, value):
    """按声明表把值规整到合法域；非法/缺失一律回落默认值。"""
    spec = SETTING_BY_KEY.get(key)
    if spec is None:
        return value
    kind = spec["kind"]
    if kind == "level":
        try:
            return max(0, min(100, int(float(value))))
        except Exception:
            return spec["default"]
    if kind == "hhmm":
        return value if _valid_hhmm(value) else spec["default"]
    if kind == "flag":
        return bool(value)
    if kind == "slots":
        return _coerce_slots(value)
    if kind == "levels":
        return _coerce_levels(value, spec["default"])
    if kind == "ids":
        return _coerce_ids(value)
    if kind == "choice":
        return value if value in spec.get("values", ()) else spec["default"]
    if kind == "int":
        try:
            n = int(value)
            lo = int(spec.get("min", 0))
            hi = int(spec.get("max", 120))
            return max(lo, min(hi, n))
        except Exception:
            return spec["default"]
    if kind == "anchors":
        if not isinstance(value, (list, tuple)):
            return []
        out = []
        for a in value:
            text = str(a or "").strip()
            if text and text not in out:
                out.append(text)
            if len(out) >= 32:
                break
        return out
    if kind == "positions":
        return _coerce_positions(value)
    return normalize_indices(value)


def _coerce_positions(value):
    """{锚点: [x, y, w, h]}；坏项直接丢掉，不让一个坏值毁掉整份配置。"""
    if not isinstance(value, dict):
        return {}
    out = {}
    for k, v in value.items():
        if not isinstance(v, (list, tuple)) or len(v) != 4:
            continue
        try:
            nums = [int(round(float(n))) for n in v]
        except Exception:
            continue
        if nums[2] <= 0 or nums[3] <= 0:      # 宽高必须为正
            continue
        out[str(k)] = nums
        if len(out) >= 16:                    # 换过的屏会留下旧锚点，封个顶
            break
    return out


def _coerce_levels(value, fallback):
    """把预设亮度规整成与默认值**等长**的 0-100 整数列表。

    长度以默认值为准（当前 3 个）：多截断、少用默认值补齐，
    这样界面永远有固定个数的按钮，不会因配置残缺而少画一个。
    """
    want = len(fallback)
    out = []
    for item in _as_iterable(value):
        if len(out) >= want:
            break
        try:
            out.append(max(0, min(100, int(float(item)))))
        except Exception:
            continue
    while len(out) < want:
        out.append(fallback[len(out)])
    return out[:want]


def _coerce_ids(value):
    """设备名锚点：去空、去重、保持顺序（如 ['DISPLAY1','DISPLAY2']）。"""
    out = []
    for item in _as_iterable(value):
        name = str(item).strip()
        if name and name not in out:
            out.append(name)
    return out


def _valid_hhmm(text):
    """'HH:MM' 合法性（时间分档、时间段编辑共用）。"""
    try:
        h, m = str(text).strip().split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
    except Exception:
        return False


def _to_index(item):
    """单个元素转成非负整数序号；非法返回 None。"""
    try:
        i = int(item)
    except Exception:
        return None
    return i if i >= 0 else None


def _split_spec(text):
    """'0, 2' → ['0','2']。"""
    return [p for p in text.replace(" ", "").split(",") if p != ""]


def _as_iterable(value):
    """不是可迭代对象就返回空列表，让上层自然得到空结果。"""
    try:
        iter(value)
    except TypeError:
        return []
    return value


def normalize_indices(value):
    """把各种写法的显示器序号整理成有序去重的 int 列表。空/all → []（表示全部）。"""
    if value is None or value == "" or value == "all":
        return []
    if isinstance(value, (int, float)):
        value = [value]
    elif isinstance(value, str):
        value = _split_spec(value)
    out = []
    for item in _as_iterable(value):
        i = _to_index(item)
        if i is not None and i not in out:
            out.append(i)
    return sorted(out)


# 能力串缓存：display key -> 能力串。能力串是面板的静态固件属性（实测 3.1s/台），
# 每次熄灭前都重读一遍曾让 DDC 熄灭耗时 6.3s/2 台 —— 缓存后只剩真正的写入
# (~62ms/台)。随配置持久化（load_config 播种 / save_config 写回），
# 换显示器后 EDID 变化 → key 不命中 → 首次实测刷新。
# R6：实际 dict 由 core.state 持有；这里保留旧名字的只读兼容（见文件末尾 __getattr__）。


def _coerce_slots(value):
    """把各种写法规整成 [{time, level}]：按时间排序、时间去重、亮度 0-100、至少一档。"""
    out, seen = [], set()
    if isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, dict):
                continue
            # 兼容旧写法：9:5 / 09：05 / 0905 统一成 09:05，顺带把"同一分钟"
            # 的不同写法去重（否则会注册两个同一时刻的触发器）
            t = normalize_time_input(item.get("time")) or ""
            if not t or t in seen:
                continue
            seen.add(t)
            try:
                lv = max(0, min(100, int(float(item.get("level", 50)))))
            except Exception:
                lv = 50
            scr = str(item.get("screen", "")).strip().lower()
            if scr not in ("", "off", "on"):
                scr = ""
            out.append({"time": t, "level": lv, "screen": scr,
                        "days": normalize_days(item.get("days"))})
    if not out:                      # 至少一档，否则调度无从判定
        out = [dict(d) for d in DEFAULT_SLOTS]
    return sorted(out, key=lambda x: parse_hhmm(x["time"], (0, 0)))


def default_config():
    """默认配置 —— 由 SETTINGS 表派生（不再手写第二份默认值）。"""
    return {spec["key"]: spec["default"] for spec in SETTINGS}


def _load_model_cache(raw):
    """从配置里取型号名缓存（非用户设置）：第二次启动起秒出型号，不再读 3s 的 DDC。"""
    models = raw.get("models")
    if isinstance(models, dict):
        return {str(k): str(v) for k, v in models.items() if v}
    return {}


# 配置读取失败时的说明（GUI 启动后显式提示；CLI 不受影响）
# R6：实际值由 core.state 持有；旧名字的只读兼容见文件末尾 __getattr__。


def _backup_bad_config(reason):
    """读坏时把原文件留一份副本，避免下一次保存把它彻底覆盖掉。"""
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = "%s.bad-%s.json" % (CONFIG_FILE, stamp)
        shutil.copy2(CONFIG_FILE, bak)
        return "配置文件读取失败（%s）。已备份为 %s，本次使用默认值。" % (reason, bak)
    except Exception:
        return "配置文件读取失败（%s）。备份失败，本次使用默认值。" % reason


def load_config():
    """读取配置；兼容 v1.0.0 的单选字段 monitor（int）→ v2 的 monitors（list）。

    逐键走 coerce_setting，所以新增一个设置**不用**再来这里加一行校验。
    读坏时不再静默吞掉：备份坏文件 + 把原因记到 CONFIG_LOAD_ERROR，由界面提示。
    """
    _state.set_load_error("")
    cfg = default_config()
    if not os.path.exists(CONFIG_FILE):
        return cfg
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("顶层不是 JSON 对象")
        # v2 -> v3 迁移：day/night 四键 -> slots 两档
        if "slots" not in raw and "day_time" in raw:
            raw = dict(raw, slots=[
                {"time": raw.get("day_time", "09:00"),
                 "level": raw.get("day_level", 80)},
                {"time": raw.get("night_time", "20:00"),
                 "level": raw.get("night_level", 30)}])
        # v1 -> v2 迁移：旧配置只有 "monitor": 0
        if "monitors" not in raw and "monitor" in raw:
            raw = dict(raw, monitors=[raw["monitor"]])
        # v2.04：旧配置里的 screen_off_method / ddc_allow_undeclared / wake_*
        # 不再迁移，也不再写回；它们不在 SETTING_BY_KEY 里，会自动被丢弃。
        # 能力串缓存播种（静态固件属性，避免每次熄灭前重读 3s/台）。
        # v1.9.0 起 key 里带 EDID 短哈希（dev#uid），旧 dev-only 键不再播种：
        # 换面板后不会沿用旧缓存，代价只是首次重新实测一次。
        # 走查 E2-02：caps 是程序自己写的缓存，但手工编辑/旧版残留可能写成
        # list/str/int。原来只防 falsy，类型错时 .items() 抛 AttributeError，
        # 被外层 except 当成"整份配置读坏"→ 用户所有设置回落默认值。
        _caps_raw = raw.get("caps")
        if isinstance(_caps_raw, dict):
            for _dev, _caps in _caps_raw.items():
                if isinstance(_caps, str) and _caps and "#" in str(_dev):
                    _state.caps_cache().setdefault(_dev, _caps)
        for key in SETTING_BY_KEY:
            if key in raw:
                cfg[key] = coerce_setting(key, raw[key])
        cfg["models"] = _load_model_cache(raw)
        diag_set_enabled(cfg.get("diag_log", True))
    except Exception as e:
        _state.set_load_error(_backup_bad_config("%s: %s" % (type(e).__name__, e)))
    return cfg


def _atomic_write_text(path, text):
    """原子写：同目录 mkstemp + os.replace，断电/并发读都不会读到半个文件。"""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_config(cfg):
    """保存配置：原子写。

    v2.01.0：不再做"屏幕状态按磁盘时间戳合并"——程序已经不记屏幕状态账
    （见 screen_verdict 的说明），没有需要跨进程协调的运行态字段，
    自然也没有"内存旧值覆盖磁盘新值"的问题。

    v2.01.20：**不迁移、不镜像**（用户决策）。配置就是程序目录那一份；
    老版本留在 APPDATA 的文件不自动搬，跨机器/跨版本各用各的。

    v2.03.0（重构 P1）：**写入失败必须留痕**。全文 18 处调用里，至少 7 处是
    `save_config(self.cfg)` 裸调用、直接丢弃返回值——磁盘只读、目录被占、
    杀软锁定都表现为"设置改了但下次启动又变回去"，且没有任何线索。
    这里统一写一条事件日志，不管调用方看不看返回值。
    """
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = dict(cfg)
        if _state.caps_cache():
            payload["caps"] = dict(_state.caps_cache())
        payload["version"] = CONFIG_VERSION
        _atomic_write_text(CONFIG_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
        return True, ""
    except Exception as e:
        # 双层保护：日志本身再失败（比如整个目录只读）也不能把异常抛回调用方，
        # 否则"保存失败"会变成"程序崩溃"，那是更糟的结果。
        try:
            _write_event_log("配置保存失败：%s" % e)
        except Exception:
            pass
        return False, str(e)


def old_config_present():
    """程序目录没有配置、而旧 APPDATA 目录有时，返回旧文件路径（否则 ""）。

    v2.01.20：不自动迁移，但要让用户知道"旧配置还在那儿"，而不是以为丢了。
    只读判断，不碰文件。
    """
    if os.path.abspath(CONFIG_DIR) == os.path.abspath(_LEGACY_CONFIG_DIR):
        return ""
    if os.path.exists(CONFIG_FILE):
        return ""
    old = os.path.join(_LEGACY_CONFIG_DIR, "config.json")
    return old if os.path.exists(old) else ""


# ---- 开机自启动（HKCU Run 键，免管理员）------------------------------------
AUTOSTART_RUN_NAME = "MonitorBrightness"


def _autostart_path():
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def get_autostart():
    """读开机自启动项。返回 (是否存在, 命令文本 or "")。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _autostart_path()) as k:
            val, _t = winreg.QueryValueEx(k, AUTOSTART_RUN_NAME)
            return True, str(val)
    except FileNotFoundError:
        return False, ""
    except Exception:
        return False, ""


def set_autostart(enable, command):
    """写/删开机自启动项。command 例：'"C:////...exe" --minimized'。"""
    import winreg
    if enable:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _autostart_path(), 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, AUTOSTART_RUN_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _autostart_path(), 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, AUTOSTART_RUN_NAME)
        except FileNotFoundError:
            pass


def __getattr__(name):
    """R6 兼容层：旧模块级名字只读转发到 core.state。

    写入请改用 core.state.set_load_error() / caps_cache()；对旧名字赋值只会
    在本模块创建一个快照属性，不再跟随状态（见 docs/交接文档.md §9.14）。
    """
    if name == "CONFIG_LOAD_ERROR":
        return _state.get_load_error()
    if name == "_CAPS_CACHE":
        return _state.caps_cache()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
