# -*- coding: utf-8 -*-
"""R3：拆 cli_diag；用确定性 mock 对比拆分前后的输出行逐字一致。"""
import importlib
import sys
import textwrap
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli

P = Path("cli.py")


def sample_output():
    lines = []
    values = {
        "load_config": lambda: {},
        "resolve_monitors": lambda cfg: [0],
        "enumerate_targets": lambda: [{"index": 0, "handle": 1, "dev": "DISPLAY1",
                                      "w": 1920, "h": 1080, "primary": True,
                                      "desc": "M"}],
        "release_targets": lambda ts: None,
        "_read_vcp": lambda h, c: (80, 100),
        "_capabilities": lambda h: object(),
        "_caps_model": lambda c: "MODEL",
        "_caps_inputs": lambda c: [1],
        "_caps_power": lambda c: [1],
        "probe_states": lambda: [{"index": 0, "dev": "DISPLAY1",
                                  "state": "on", "why": "OK"}],
        "ccd_probe_topology": lambda: [{"active": True, "vdd": False,
                                        "friendly": "MODEL", "anchor": "A1",
                                        "gdi": "DISPLAY1", "aliases": []}],
        "topology_state_for": lambda cfg, rows: {"detail": "OK"},
        "query_task_status": lambda: "Ready",
        "query_task_runtime": lambda: "RUNTIME=NEVER",
        "task_match_reason": lambda cfg: "",
        "_task_action": lambda cfg: ("exe", "arg"),
    }
    patches = [unittest.mock.patch.object(cli, "_out", side_effect=lines.append)]
    patches += [unittest.mock.patch.object(cli, n, v) for n, v in values.items()]
    for p in patches:
        p.start()
    try:
        try:
            cli.cli_diag()
        except SystemExit:
            pass
    finally:
        for p in reversed(patches):
            p.stop()
    return lines


def dedent4(s):
    s = textwrap.dedent(s)
    return "".join("    " + ln if ln.strip() else ln for ln in s.splitlines(keepends=True))


def main():
    before = sample_output()
    src = P.read_text(encoding="utf-8")
    start = src.index("def cli_diag():")
    end = src.index("\ndef cli_ensure_schedule():", start)
    body = src[start:end]

    i_header = body.index('    _out("MonitorBrightness v%s 诊断报告"')
    i_targets = body.index("    targets = enumerate_targets()")
    i_try = body.index("    try:\n", i_targets)
    i_sel = body.index('        _out("")\n        _out("二、', i_try)
    i_sched = body.index('        _out("")\n        _out("五、', i_sel)
    i_finally = body.index("    finally:", i_sched)

    header = body[i_header:i_targets].replace("_out(", "lines.append(")
    target = dedent4(body[i_try + len("    try:\n"):i_sel]).replace("_out(", "lines.append(")
    selection = dedent4(body[i_sel:i_sched]).replace("_out(", "lines.append(")
    schedule = dedent4(body[i_sched:i_finally]).replace("_out(", "lines.append(")

    new = '''def _diag_header_lines():
    lines = []
''' + header + '''    return cfg, lines


def _diag_target_lines(targets):
    lines = []
''' + target + '''    return lines


def _diag_selection_lines(cfg):
    lines = []
''' + selection + '''    return lines


def _diag_schedule_lines(cfg):
    lines = []
''' + schedule + '''    return lines


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

'''
    P.write_text(src[:start] + new + src[end:], encoding="utf-8", newline="\n")
    importlib.reload(cli)
    after = sample_output()
    if before != after:
        P.write_text(src, encoding="utf-8", newline="\n")
        importlib.reload(cli)
        raise SystemExit("诊断输出不一致，已回滚")
    print("cli_diag 已拆 5 段，%d 行输出逐字一致" % len(before))


if __name__ == "__main__":
    main()
