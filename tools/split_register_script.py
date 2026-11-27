# -*- coding: utf-8 -*-
"""R3：拆 build_register_script；生成产物逐字节比对后才落盘。"""
import ast
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import schedule

P = Path("schedule.py")


def samples():
    base = {"slots": [{"time": "08:30", "level": 80, "screen": "", "days": []},
                      {"time": "21:00", "level": 0, "screen": "", "days": []}]}
    out = []
    for on in (True, False):
        cfg = dict(base, catch_up_on_logon=on)
        out.append(schedule.build_register_script(cfg))
    return out


def main():
    before = samples()
    src = P.read_text(encoding="utf-8")
    start = src.index("def build_register_script(cfg):")
    end = src.index("\ndef register_tasks(cfg):", start)
    body = src[start:end]

    i_header = body.index("    lines = [")
    i_task = body.index('    lines += [\n        ")",\n', i_header)
    i_catch = body.index('    if cfg.get("catch_up_on_logon"):', i_task)
    i_report = body.index('    lines += [\n        "$t = Get-ScheduledTask', i_catch)
    i_ret = body.index("    return _ps_script(lines)", i_report)

    header = body[i_header:i_task] + "    return lines\n"
    task = body[i_task:i_catch].replace("    lines += [", "    return [", 1)
    catch = (body[i_catch:i_report]
             .replace('    if cfg.get("catch_up_on_logon"):', "    if enabled:", 1))
    catch = "    lines = []\n" + catch + "    return lines\n"
    report = body[i_report:i_ret].replace("    lines += [", "    lines = [", 1) + "    return lines\n"

    new = '''def _register_header_lines(exe, arg, slots):
    """PowerShell 头：执行策略、动作、设置、清遗留任务、触发器数组。"""
''' + header + '''
def _register_task_lines(slots):
    """主定时任务：原地更新优先，失败回退删后建。"""
''' + task + '''
def _register_catchup_lines(enabled):
    """登录补跑任务：独立 try/catch，失败不影响主任务。"""
''' + catch + '''
def _register_report_lines():
    """回读段：SWA / 触发点数 / 上次下次运行 / 补跑任务状态。"""
''' + report + '''
def build_register_script(cfg):
    """生成注册脚本：一条定时任务挂 N 个触发器（每档一个）、可选登录补跑任务。

    v1.9.0 起：任务已存在时优先 Set-ScheduledTask **原地更新**（保留上次运行记录），
    失败才回退到 Unregister + Register。旧实现每次都先删后建，会把 LastRunTime
    重置成"从未运行"，也让用户误以为定时没生效。
    """
    exe, arg = _task_action(cfg)
    slots = cfg.get("slots", [])
    lines = _register_header_lines(exe, arg, slots)
    lines += _register_task_lines(slots)
    lines += _register_catchup_lines(bool(cfg.get("catch_up_on_logon")))
    lines += _register_report_lines()
    return _ps_script(lines)

'''
    P.write_text(src[:start] + new + src[end:], encoding="utf-8", newline="\n")
    importlib.reload(schedule)
    after = samples()
    if before != after:
        P.write_text(src, encoding="utf-8", newline="\n")
        importlib.reload(schedule)
        raise SystemExit("生成产物不一致，已回滚")
    print("build_register_script 已拆 4 段，%d 组样例逐字节一致" % len(before))


if __name__ == "__main__":
    main()
