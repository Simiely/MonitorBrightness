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


def _run_cli(args):
    """处理命令行分支；返回 True 表示已处理，main() 应直接返回。"""
    if not args:
        return False
    cmd = args[0]
    if cmd == "--diag-log":
        # 诊断日志（临时命令，v2.01.4）：直接打印最近若干行
        lines = diag_dump(200)
        _out("diag.log：%s（%d 行）" % (_diag_file(), len(lines)))
        for line in lines:
            _out(line)
        return True
    if cmd == "--diag-export":
        # 导出诊断包（临时命令）：日志 + 配置打成一个 zip
        path, err = diag_export()
        _out(err or ("已导出：%s" % path))
        return True
    if cmd == "--set" and len(args) >= 3:
        cli_set(args[1], args[2])
        return True
    if cmd == "--apply-schedule":
        cli_apply_schedule()
        return True
    if cmd == "--screen" and len(args) >= 2:
        arg = args[1].strip().lower()
        if arg == "status":
            # 与界面同一套结论：当场读、不读任何记账（v2.01.0）
            v = screen_verdict(load_config())
            _out(v["text"])
            if v.get("detail"):
                _out("  " + v["detail"])
            return True
        action = parse_screen_action(args[1])
        if action is None:
            _out("--screen 参数需为 off 或 on")
            sys.exit(2)
        cli_screen(action)
        return True
    if cmd == "--list":
        cli_list()
        return True
    if cmd == "--probe":
        cli_probe()
        return True
    if cmd == "--diag":
        cli_diag()
        return True
    if cmd == "--selftest":
        cli_selftest()
        return True
    if cmd == "--ensure-schedule":
        cli_ensure_schedule()
        return True
    return False
