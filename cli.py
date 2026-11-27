# -*- coding: utf-8 -*-
"""命令行模式（v2.03.0 R3，从 monitor_brightness.py 逐字搬出）。

供任务计划静默调用与排障使用；GUI 入口 main() 仍留在主文件。
"""
import sys

from core.config import APP_VERSION, load_config
from core.logging import _diag_file, diag_enabled
from core.paths import APP_DIR, CONFIG_FILE, LOG_DIR
from core.util import _err, _out
from domain.slots import _minutes, _slot_minutes, active_slot
from hw.ccd import ccd_probe_topology, screen_verdict, topology_state_for
from hw.ddc import (
    NO_MONITOR_MSG, VCP_BRIGHTNESS, VCP_INPUT_SOURCE, VCP_POWER_MODE,
    _capabilities, _caps_inputs, _caps_model, _caps_power, _read_vcp,
    enumerate_targets, monitor_label, probe_states, release_targets,
    set_brightness_many,
)
from hw.power import take_wake_info
from schedule import (
    _task_action, apply_schedule, ensure_task_registration, query_task_runtime,
    query_task_status, task_match_reason,
)
from service import resolve_monitors, screen_off_cfg, screen_wake_entry

# 0x60 输入源取值（只列常见项，其余照原样显示数字）
INPUT_LABEL = {1: "模拟1", 2: "模拟2", 3: "DVI-1", 4: "DVI-2", 5: "复合1",
               6: "复合2", 15: "DP-1", 16: "DP-2", 17: "HDMI-1", 18: "HDMI-2",
               19: "HDMI-3"}

# ============================================================
# 六、命令行模式（供任务计划静默调用）
# ============================================================
def parse_level_arg(text):
    """严格解析命令行里的亮度：必须是 0-100 的整数，非法返回 None。

    ⚠️ 不能复用 clamp_level —— 它会把 'abc' 静默变成 0，
    那样任务计划里参数写错就会把屏幕直接调黑。
    """
    try:
        v = int(str(text).strip())
    except Exception:
        return None
    return v if 0 <= v <= 100 else None

def parse_index_arg(spec):
    """解析命令行里的显示器序号：'all' → []（全部）；'0,1' → [0, 1]；非法 → None。

    同样不能退化成"全部"：写错序号就去动所有显示器是危险的。
    """
    s = str(spec).strip().lower()
    if s in ("all", "*"):
        return []
    parts = [p for p in s.replace(" ", "").split(",") if p != ""]
    if not parts:
        return None
    out = []
    for p in parts:
        try:
            i = int(p)
        except Exception:
            return None
        if i < 0:
            return None
        if i not in out:
            out.append(i)
    return sorted(out)

def cli_set(spec, value):
    level = parse_level_arg(value)
    if level is None:
        _err("亮度必须是 0-100 的整数，收到：%r" % (value,))
        sys.exit(2)
    indices = parse_index_arg(spec)
    if indices is None:
        _err("显示器序号不合法，应为 all 或 0,1 这样的写法，收到：%r" % (spec,))
        sys.exit(2)
    try:
        ok, errs = set_brightness_many(indices, level)
        for i, e in errs:
            _err("显示器 %s: %s" % (i, e))
        sys.exit(0 if ok else 1)
    except Exception as e:
        _err(e)
        sys.exit(1)

def cli_apply_schedule():
    # 走查 P-02 补充：注销任务失败时任务可能残留，但配置已 enabled=False。
    # 到这里必须再确认一次用户意图，否则"已关闭定时"仍会熄屏。
    try:
        cfg = load_config()
        if not cfg.get("enabled"):
            _out("定时已关闭（enabled=false），本次不执行档位")
            sys.exit(0)
    except Exception:
        pass                      # 读不到配置时照旧执行，避免升级为阻断
    try:
        level, screen, ok, errs = apply_schedule()
        for i, e in errs:
            _err("显示器 %s: %s" % (i, e))
        if screen:
            _out("屏幕动作: %s" % screen)
        for line in take_wake_info():
            _out(line)
        sys.exit(0 if ok else 1)
    except Exception as e:
        _err(e)
        sys.exit(1)

def cli_list():
    """列出显示器（含设备名 / 分辨率 / 主副屏 / 当前输入源）。"""
    targets = enumerate_targets()
    if not targets:
        _err(NO_MONITOR_MSG)
        sys.exit(1)
    try:
        for t in targets:
            line = monitor_label(t["index"], (t["dev"], t["w"], t["h"], t["primary"]))
            src = _read_vcp(t["handle"], VCP_INPUT_SOURCE)
            if src:
                line += " · %s" % INPUT_LABEL.get(src[0], "输入源 %d" % src[0])
            _out(line)
    finally:
        release_targets(targets)
    sys.exit(0)

def cli_probe():
    """打印"亮还是灭"的结论 + 每台的回读证据（只读 DDC，不写任何寄存器）。"""
    cfg = load_config()
    try:
        states = probe_states()
    except Exception as e:
        _err(e)
        sys.exit(1)
    v = screen_verdict(cfg, states)
    _out(v["text"])
    for s in states:
        _out("  显示器 %d（%s）：回读 %s — %s"
             % (s["index"], s["dev"],
                {"on": "亮", "off": "灭"}.get(s["state"], "未知"), s["why"]))
    if states:
        _out("  说明：0xD6 只会假阴性（待机时仍报 1），所以「报灭」可信、"
             "「报亮」不能证明面板亮着；程序不再记账，读不到就说不知道")
    if v.get("detail"):
        _out("  " + v["detail"])
    sys.exit(0)

def _diag_header_lines():
    lines = []
    lines.append("MonitorBrightness v%s 诊断报告" % APP_VERSION)
    lines.append("Python %s · 打包态=%s · 配置=%s"
         % (sys.version.split()[0], bool(getattr(sys, "frozen", False)), CONFIG_FILE))
    cfg = load_config()
    lines.append("配置：presets=%s · 熄灭方式=%s（屏幕亮灭每次现读，不记账）"
         % (cfg.get("presets"), cfg.get("screen_off_method")))
    lines.append("配置里的显示器选择：monitors=%s · monitor_ids=%s · monitor_anchors=%s"
         % (cfg.get("monitors"), cfg.get("monitor_ids"), cfg.get("monitor_anchors")))
    lines.append("接回位置记录：%s" % (cfg.get("monitor_positions") or "{}"))
    lines.append("锚点优先解析结果：%s" % (resolve_monitors(cfg) or "[]（= 全部）"))
    lines.append("日志目录：%s（%s）" % (LOG_DIR, "程序同目录" if LOG_DIR == APP_DIR else "回退到 APPDATA"))
    lines.append("诊断日志：%s（%s，用 --diag-log 看内容）"
         % (_diag_file(), "开启" if diag_enabled() else "已关闭"))

    return cfg, lines


def _diag_target_lines(targets):
    lines = []
    lines.append("")
    lines.append("一、枚举结果（共 %d 个输出，即界面上的行数）" % len(targets))
    for t in targets:
        pow_ = _read_vcp(t["handle"], VCP_POWER_MODE)
        src = _read_vcp(t["handle"], VCP_INPUT_SOURCE)
        lum = _read_vcp(t["handle"], VCP_BRIGHTNESS)
        caps = _capabilities(t["handle"])
        lines.append("  [%d] %s  %d×%d  %s  desc=%r"
             % (t["index"], t["dev"], t["w"], t["h"],
                "主屏" if t["primary"] else "副屏", t["desc"]))
        lines.append("      电源 0xD6=%s · 输入源 0x60=%s · 亮度 0x10=%s"
             % (pow_[0] if pow_ else "不可读", src[0] if src else "不可读",
                lum[0] if lum else "不可读"))
        lines.append("      型号=%r · 支持输入源=%s · 支持电源值 0xD6=%s"
             % (_caps_model(caps), _caps_inputs(caps) or "读不到",
                _caps_power(caps) or "读不到"))

    return lines


def _diag_selection_lines(cfg):
    lines = []
    lines.append("")
    lines.append("二、程序实际会操作的序号（按设备名锚点解析）")
    lines.append("  resolve_monitors() -> %s" % (resolve_monitors(cfg) or "[]（= 全部）"))

    lines.append("")
    lines.append("三、亮灭判定")
    for s in probe_states():
        lines.append("  [%d] %s -> %s（%s）"
             % (s["index"], s["dev"],
                {"on": "亮", "off": "灭"}.get(s["state"], "未知"), s["why"]))

    lines.append("")
    lines.append("四、显示拓扑（只读，含被断开的屏）")
    ccd_rows = ccd_probe_topology()
    if not ccd_rows:
        lines.append("  未取到 CCD 拓扑（非控制台会话 / 驱动不支持？）")
    for r in ccd_rows:
        extra = [a for a in r.get("aliases", []) if a != r["gdi"]]
        lines.append("  %s %s  %s  锚点=%s  gdi=%s%s" %
             ("ACTIVE " if r["active"] else "inactive",
              "虚拟屏" if r["vdd"] else "物理屏",
              r["friendly"] or "(无友好名)", r["anchor"], r["gdi"] or "-",
              "  (同源: %s)" % ",".join(extra) if extra else ""))
    lines.append("  折叠状态：%s" % topology_state_for(cfg, ccd_rows)["detail"])

    return lines


def _diag_schedule_lines(cfg):
    lines = []
    lines.append("")
    lines.append("五、定时任务")
    for line in (query_task_status() or "").splitlines():
        lines.append("  " + line)
    for line in (query_task_runtime() or "").splitlines():
        lines.append("  " + line)
    reason = task_match_reason(cfg)
    lines.append("  任务定义与当前进程：%s" % (reason or "一致"))
    lines.append("  当前进程应写：%s | %s" % _task_action(cfg))
    return lines


def _diag_collect():
    cfg, lines = _diag_header_lines()
    targets = enumerate_targets()
    if not targets:
        return lines + ["", NO_MONITOR_MSG], 1
    try:
        lines += _diag_target_lines(targets)
        lines += _diag_selection_lines(cfg)
        lines += _diag_schedule_lines(cfg)
    finally:
        release_targets(targets)
    return lines, 0


def cli_diag():
    """打印一份只读诊断报告（给"另一台电脑上点不亮"取证用）。

    刻意把"同一个物理屏被枚举成几个输出""现在每个输出的输入源是什么"
    都打出来：DP+HDMI 双线接法下，这两项直接决定"点亮"会不会切到空口。
    """
    lines, code = _diag_collect()
    for line in lines:
        _out(line)
    sys.exit(code)


def cli_ensure_schedule():
    """按当前配置/当前 exe 重建计划任务（供发版部署后无界面自愈；也可排障用）。"""
    cfg = load_config()
    _out("配置：enabled=%s · slots=%s"
         % (cfg.get("enabled"), [s.get("time") for s in cfg.get("slots", [])]))
    ok, msg = ensure_task_registration(cfg, force=("--force" in sys.argv))
    _out(msg or "任务定义与当前程序一致，无需重建")
    if cfg.get("enabled"):
        for line in (query_task_runtime() or "").splitlines():
            _out("  " + line)
    sys.exit(0 if ok else 1)

def cli_selftest():
    """打印 N 档调度自检表（不触碰显示器）。"""
    cfg = load_config()
    slots = _slot_minutes(cfg)
    _out("配置：%s" % " / ".join("%s->%s%%" % (x["time"], x["level"])
                                 for x in cfg.get("slots", [])))
    _out("当前档：%s%%" % active_slot(slots, _minutes())[1])
    for hh, mm in [(0, 0), (8, 28), (8, 30), (8, 32), (12, 0),
                   (20, 58), (21, 0), (21, 2), (23, 59)]:
        t = hh * 60 + mm
        _out("  %02d:%02d -> %s%%" % (hh, mm, active_slot(slots, t)[1]))
    sys.exit(0)

def parse_screen_action(text):
    """'off'/'on' -> 规范动作名；其他 -> None（供 CLI 与测试复用）。"""
    t = str(text).strip().lower()
    return t if t in ("off", "on") else None

def cli_screen(action):
    """熄灭/点亮选中的显示器（配置里的选择；空 = 全部）。

    v2.01.0：不再把动作结果记账落盘——动作做完就是做完了，
    屏幕现在什么状态要靠 screen_verdict 现场读。
    """
    cfg = load_config()
    targets = resolve_monitors(cfg)
    if action == "off":
        ok, errs = screen_off_cfg(cfg, targets)
        _out("已熄灭显示器" if ok else "熄灭失败")
    else:
        ok, errs = screen_wake_entry(cfg, targets)
        _out("已点亮显示器" if ok else "点亮失败")
        for line in take_wake_info():
            _out(line)
    for i, e in errs:
        _out("显示器 %d：%s" % (i, e))
    if errs and not ok:
        sys.exit(1)
