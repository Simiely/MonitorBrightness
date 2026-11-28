# -*- coding: utf-8 -*-
"""CLI 分流（P1-5，从 app/base.py 逐字搬出）。

只处理 `--set/--list/--probe/--diag/--screen/--selftest/--ensure-schedule`
这些无界面命令；GUI 启动仍在 app/base.py。
"""

import sys

from cli import (
    cli_apply_schedule, cli_diag, cli_ensure_schedule, cli_list, cli_probe,
    cli_screen, cli_selftest, cli_set, parse_screen_action,
)
from core.config import load_config
from core.logging import _diag_file, diag_dump, diag_export
from core.util import _out
from hw.ccd import screen_verdict

# 存名字而不是函数对象：测试 patch `app.cli_entry.cli_list` 后，
# 运行时再取全局名才能命中 mock（函数对象会被 import 时提前绑定）。
_SIMPLE_COMMANDS = {
    "--apply-schedule": "cli_apply_schedule",
    "--list": "cli_list",
    "--probe": "cli_probe",
    "--diag": "cli_diag",
    "--selftest": "cli_selftest",
    "--ensure-schedule": "cli_ensure_schedule",
}


def _run_diag_log(args):
    lines = diag_dump(200)
    _out("diag.log：%s（%d 行）" % (_diag_file(), len(lines)))
    for line in lines:
        _out(line)


def _run_diag_export(args):
    path, err = diag_export()
    _out(err or ("已导出：%s" % path))


def _run_screen_cli(arg):
    """`--screen status|off|on`；参数非法时按原行为退出码 2。"""
    arg = arg.strip().lower()
    if arg == "status":
        # 与界面同一套结论：当场读、不读任何记账（v2.01.0）
        v = screen_verdict(load_config())
        _out(v["text"])
        if v.get("detail"):
            _out("  " + v["detail"])
        return
    action = parse_screen_action(arg)
    if action is None:
        _out("--screen 参数需为 off 或 on")
        sys.exit(2)
    cli_screen(action)


def _run_cli(args):
    """处理命令行分支；返回 True 表示已处理，main() 应直接返回。"""
    if not args:
        return False
    cmd = args[0]
    if cmd == "--diag-log":
        _run_diag_log(args)
        return True
    if cmd == "--diag-export":
        _run_diag_export(args)
        return True
    if cmd == "--set":
        if len(args) < 3:
            return False
        cli_set(args[1], args[2])
        return True
    if cmd == "--screen":
        if len(args) < 2:
            return False
        _run_screen_cli(args[1])
        return True
    handler = _SIMPLE_COMMANDS.get(cmd)
    if handler is None:
        return False
    globals()[handler]()
    return True
