# -*- coding: utf-8 -*-
"""P1-8：把 3801 行的 tests/test_core.py 拆成 support + 分类 cases。

保留 tests/test_core.py 作为 10 行聚合入口，既有
`py -m unittest tests.test_core` 命令不变，也不会被 discover 重复收集。
"""
import ast
from pathlib import Path

TESTS = Path("tests")
SRC = TESTS / "test_core.py"
src = SRC.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src, filename=str(SRC))

classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
def span(node):
    return "".join(lines[node.lineno - 1:node.end_lineno]).rstrip()

GROUPS = {
    "cases_config.py": [
        "ConfigTest", "TimeTest", "StartupCheckTest",
        "DiagLogTest", "DiagExportTest", "SessionEventsTest",
    ],
    "cases_schedule.py": [
        "TaskTest", "AddButtonRegression", "RebuildNoticeRegression",
        "RegisterScriptTest", "ScheduleSubprocessTest", "WeekdayScheduleTest",
        "MainDispatchTest", "CliSmokeTest",
    ],
    "cases_screen.py": [
        "BusyQueueTest", "WakeSelectionTest", "FastFlipTest",
        "TopologyVerdictTest", "MonitorIdentityTest", "TopologyGuiTest",
        "SelectionAnchorsTest", "WindowFollowTest", "RestoreSelectionTest",
        "VddNameTest", "DefaultLayoutTest",
    ],
    "cases_ui.py": [
        "UiDecisionTest", "UiSmokeTest", "WheelRouterTest", "TooltipTest",
        "RememberLayoutTest", "AppMethodTest", "ApplyCallbackTest",
    ],
    "cases_topology.py": [
        "TopologyActionTest", "TopologySubmitShapeTest",
        "TopologySourceModeSynthesisTest", "TopologySnapshotShapeTest",
        "TopologyPositionTest", "TopologyScheduleTest", "TopologyPlanOnTest",
    ],
    "cases_regression.py": [
        "WalkthroughRound2Test", "WalkthroughFixTest",
    ],
}
all_used = {name for names in GROUPS.values() for name in names}
assert all_used <= set(classes), sorted(all_used - set(classes))
assert set(classes) - all_used <= {"_PatchAll", "TempConfigCase", "_FakeU32", "_TopoFixture"}, \
    sorted(set(classes) - all_used)

support_head = "".join(lines[:141]).rstrip() + "\n"
support = support_head + "\n\n" + "\n\n".join([
    span(classes["TempConfigCase"]),
    span(funcs["_row_button_texts"]),
    span(classes["_FakeU32"]),
    span(classes["_TopoFixture"]),
]) + "\n"
(TESTS / "support.py").write_text(support, encoding="utf-8")

HEADER = '''# -*- coding: utf-8 -*-
"""P1-8 拆分出的测试用例；由 tests/test_core.py 聚合运行。"""

from tests.support import *  # noqa: F401,F403
from tests.support import (  # noqa: F401
    _FakeU32, _PATCH_MODULES, _TopoFixture, _UNSET, _row_button_texts,
)


'''
for filename, names in GROUPS.items():
    body = "\n\n\n".join(span(classes[name]) for name in names)
    (TESTS / filename).write_text(HEADER + body + "\n", encoding="utf-8")

agg = '''# -*- coding: utf-8 -*-
"""测试入口聚合（P1-8）。实际用例已按域拆到 tests/cases_*.py。

保留本文件是为了 `py -m unittest tests.test_core` 仍然可用；
不要把 cases 文件命名成 test_*.py，否则 discover 会重复收集。
"""

'''
for filename in GROUPS:
    agg += "from tests.%s import *  # noqa: F401,F403\n" % filename[:-3]
SRC.write_text(agg, encoding="utf-8")
print("support + %d case files; test_core.py is now %d lines" % (
    len(GROUPS), len(agg.splitlines())))
