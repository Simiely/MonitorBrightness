# -*- coding: utf-8 -*-
"""R3：把 CLI 命令逐字搬到 cli.py；main() 入口留在主文件。"""
import ast
from pathlib import Path

SRC = Path("monitor_brightness.py")
DST = Path("cli.py")

FUNCS = [
    "parse_level_arg", "parse_index_arg", "cli_set", "cli_apply_schedule",
    "cli_list", "cli_probe", "cli_diag", "cli_ensure_schedule", "cli_selftest",
    "parse_screen_action", "cli_screen",
]
CONSTS = ["INPUT_LABEL"]
NAMES = FUNCS + CONSTS

HEADER = '''# -*- coding: utf-8 -*-
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

'''


def main():
    src = SRC.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    nodes = {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            nodes[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    nodes[t.id] = n

    picked, ranges = [], []
    for nm in NAMES:
        n = nodes.get(nm)
        if n is None:
            raise SystemExit("缺节点: %s" % nm)
        start = n.lineno - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        picked.append((n.lineno, "".join(lines[start:n.end_lineno])))
        ranges.append((start, n.end_lineno))
    picked.sort()
    DST.write_text(HEADER + "\n\n".join(c.rstrip("\n") for _, c in picked) + "\n",
                   encoding="utf-8", newline="\n")

    for a, b in sorted(ranges, reverse=True):
        del lines[a:b]

    block = ("from cli import (                                  # noqa: E402\n"
             "    " + ", ".join(NAMES) + ",\n"
             ")\n")
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("from schedule import ("))
    while not lines[idx].rstrip().endswith(")"):
        idx += 1
    lines.insert(idx + 1, block)
    SRC.write_text("".join(lines), encoding="utf-8", newline="\n")
    print("cli：搬移 %d 个节点" % len(NAMES))


if __name__ == "__main__":
    main()
