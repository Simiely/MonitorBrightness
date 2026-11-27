# -*- coding: utf-8 -*-
"""C3 实验：计划任务里只查一次 CCD，把结果写日志，不改任何显示设置。"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import topology_probe as tp

log_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "task-probe.log")
try:
    u32, _ = tp._api()
    rc, paths, _modes = tp.query_paths(u32)
    line = ("%s rc=%d pathCount=%d active=%d pid=%s user=%s\n" % (
        datetime.datetime.now().isoformat(timespec="seconds"), rc, len(paths),
        sum(1 for p in paths if p.flags & tp.PATH_ACTIVE), os.getpid(),
        os.environ.get("USERNAME", "?")))
except Exception as e:
    line = "%s EXC %s: %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"),
                               type(e).__name__, e)
with open(log_path, "a", encoding="utf-8") as f:
    f.write(line)
print(line.strip())
