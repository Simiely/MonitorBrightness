# -*- coding: utf-8 -*-
"""事件日志与诊断日志（v2.03.0 R1，从 monitor_brightness.py 搬出）。

函数体逐字搬移，零行为变化。模块级状态（_SESSION_EVENTS / _DIAG_ON 等）
也一并搬来 —— 测试与界面通过主文件的再导出访问同一个对象。

⚠️ 依赖方向：本模块在 diag_export() **函数内部**局部 import core.config，
   避免与 config → logging 的事件日志依赖形成模块级环。
"""
import json
import os
import sys
import threading
import zipfile
from datetime import datetime

from core import state as _state
from core.paths import APP_DIR, CONFIG_DIR, CONFIG_FILE, EVENTS_FILE, LOG_DIR


def _write_event_log(text):
    """追加一行事件日志（线程安全）。返回写入的那一行。

    ⚠️ 只用于旁路记账（任务重建/配置恢复/降级重试等），不得在熄灭、点亮
    调用链里写下正常成功的实现细节 —— 那些进 diag.log（dlog），
    因为它们对用户没有意义，只会让界面看起来"一直在报东西"。
    """
    line = "%s %s" % (datetime.now().strftime("%m-%d %H:%M"), text)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with _EVENT_LOCK:
            try:
                if os.path.getsize(EVENTS_FILE) > 256 * 1024:
                    os.replace(EVENTS_FILE, EVENTS_FILE + ".1")
            except OSError:
                pass
            with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _events = _state.session_events()
            _events.append(line)
            del _events[:-40]
    except Exception:
        pass
    return line


def read_session_events(limit=3):
    """**本次运行**产生的事件最后几条。

    不读 events.log 文件：那里面是历史，界面一开机就把上周的记录摆出来，
    用户会以为"一直报错"。历史留给 --diag / 诊断包，界面只反映这一次。
    """
    return list(_state.session_events()[-limit:])


_EVENT_LOCK = threading.Lock()
# R6：本次进程写的事件由 core.state 持有，旧名字只读兼容见文件末尾 __getattr__。


def read_recent_events(limit=3):
    """读事件日志文件最后几行（给 --diag / 诊断包用，**不再直接喂界面**）。"""
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = [x.strip() for x in f.readlines() if x.strip()]
        return lines[-limit:]
    except Exception:
        return []


# ============================================================================
# 诊断日志（临时模块，v2.01.4 引入）
# ----------------------------------------------------------------------------
# 目的：趁问题还没收敛，把"当时到底读到了什么、算了什么、提交了什么、回来什么"
# 原样落盘，省掉来回猜。**问题收敛后整块删掉**，删除清单只有四条：
#   1) 删掉本节（DIAG_FILE ~ diag_dump，含 _DIAG_* 全局量）；
#   2) 删掉 SETTINGS 里的 "diag_log" 一项（grep diag_log）；
#   3) 删掉界面里两处（设置页的开关行、日志卡片的「打开目录」按钮）；
#   4) `grep -n "dlog(" monitor_brightness.py` 把埋点行整行删掉。
# 为了好删，这里只暴露两个函数、埋点一律写成单行 dlog(...)，不写多行调用。
#
# 格式是 JSON Lines：一行一个 JSON，字段固定 t/pid/th/lv/tag + 自定义键。
# 排查时 `findstr /c:"\"tag\": \"topo.on\"" diag.log` 就能把一次接回的全过程串起来。
# ============================================================================
_DIAG_LOCK = threading.Lock()
# R6：开关由 core.state 持有（diag_log 同步）；旧名字只读兼容见文件末尾。
_DIAG_LIMIT = 512 * 1024        # 超过就轮转到 diag.log.1，只留一份


def _diag_file():
    """每次现算：测试会把 LOG_DIR 指到临时目录，不能在导入时就定死。"""
    return os.path.join(LOG_DIR, "diag.log")


def diag_set_enabled(value):
    """配置变化时同步开关（load_config / apply_setting 各调一次就够了）。"""
    _state.set_diag_on(value)


def diag_enabled():
    return _state.get_diag_on()


def dlog(tag, **fields):
    """写一条诊断日志。未启用时是纯函数调用，零 IO。

    tag 用点号分段，便于按前缀筛：
      app.*   界面动作（点了哪个按钮、哪个下标）
      topo.*  拓扑读取 / 计划 / 提交 / 回读
      ccd.*   和 SetDisplayConfig 打交道的细粒度动作
      sch.*   定时档位判定
    """
    if not _state.get_diag_on():
        return
    rec = {"t": datetime.now().strftime("%m-%d %H:%M:%S.%f")[:-3],
           "pid": os.getpid(), "th": threading.current_thread().name,
           "tag": tag}
    for k, v in fields.items():
        if isinstance(v, (int, float, bool)) or v is None:
            rec[k] = v
            continue
        # 结构化值原样留（事后好筛），太大才退化成字符串
        if isinstance(v, (list, tuple, dict)):
            try:
                as_json = list(v) if isinstance(v, tuple) else v
                if len(json.dumps(as_json, ensure_ascii=False)) <= 600:
                    rec[k] = as_json
                    continue
            except Exception:
                pass
        text = str(v)
        rec[k] = text if len(text) <= 600 else text[:600] + "…"
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False)
        path = _diag_file()
        with _DIAG_LOCK:
            try:
                if os.path.getsize(path) > _DIAG_LIMIT:
                    os.replace(path, path + ".1")
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def diag_dump(limit=400):
    """给界面/CLI 用：返回最近 limit 行原文（不解析，原样给）。"""
    try:
        with open(_diag_file(), "r", encoding="utf-8", errors="replace") as f:
            return [x.rstrip("\n") for x in f.readlines()[-limit:]]
    except Exception:
        return []


def diag_export(dest_dir=None):
    """把日志 + 配置打成一个 zip，出问题直接把这个文件发出去。

    只装排障需要的东西：三份日志（含轮转的 .1）、config.json、一份环境摘要。
    返回 (zip 路径, 错误)。日志目录优先用程序同目录 —— 所以 zip 也放那里，
    和 exe 并排，用户一找就能看到。
    """
    # R1：局部 import 打破 logging ↔ config 的模块级循环依赖。
    from core.config import APP_VERSION, load_config
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = dest_dir or LOG_DIR
    try:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "MonitorBrightness_诊断包_%s.zip" % stamp)
        cfg = load_config()
        summary = [
            "版本：v%s" % APP_VERSION,
            "打包态：%s" % bool(getattr(sys, "frozen", False)),
            "Python：%s" % sys.version.split()[0],
            "程序目录：%s" % APP_DIR,
            "日志目录：%s" % LOG_DIR,
            "配置目录：%s" % CONFIG_DIR,
            "熄灭方式：%s" % cfg.get("screen_off_method"),
            "锚点：%s" % (cfg.get("monitor_anchors") or []),
            "位置记录：%s" % (cfg.get("monitor_positions") or {}),
            "诊断日志：%s" % ("开启" if diag_enabled() else "关闭"),
            "导出时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("diag.log", "diag.log.1", "events.log",
                         "events.log.1", "error.log"):
                src = os.path.join(LOG_DIR, name)
                if os.path.exists(src):
                    z.write(src, name)
            if os.path.exists(CONFIG_FILE):
                z.write(CONFIG_FILE, "config.json")
            z.writestr("环境.txt", "\n".join(summary) + "\n")
        dlog("app.export", path=path)
        return path, ""
    except Exception as e:
        return "", "%s: %s" % (type(e).__name__, e)


# ============================================================================
# 诊断日志结束（以上整块可删）
# ============================================================================


def __getattr__(name):
    """R6 兼容层：旧模块级名字只读转发到 core.state。

    写入请改用 core.state.set_diag_on() / session_events()；对旧名字赋值只会
    创建快照属性，不再跟随状态（见 docs/交接文档.md §9.14）。
    """
    if name == "_SESSION_EVENTS":
        return _state.session_events()
    if name == "_DIAG_ON":
        return _state.get_diag_on()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))

