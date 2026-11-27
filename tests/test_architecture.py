# -*- coding: utf-8 -*-
"""R5 机器守卫：分层 import、文件行数、函数行数 ratchet。

只做 AST 静态检查，不 import 主程序（避免测试机上弹 Tk / 碰硬件）。
例外必须写进 `_FUNC_EXCEPTIONS` 并带理由，新增例外要在 review 里说明。
"""
import ast
import pathlib
import unittest
import warnings

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE_DIRS = ("core", "domain", "hw", "ui", "app")
SOURCE_FILES = [ROOT / n for n in (
    "service.py", "schedule.py", "schedule_report.py", "cli.py",
    "monitor_brightness.py",
)]

# 依赖只准向下：值 = 当前层禁止 import 的层
_FORBIDDEN = {
    "core": {"hw", "ui", "app", "service", "schedule", "cli", "entry", "tkinter"},
    "domain": {"core", "hw", "ui", "app", "service", "schedule", "cli", "entry", "tkinter"},
    "hw": {"ui", "app", "service", "schedule", "cli", "entry"},
    "service": {"ui", "app", "schedule", "cli", "entry"},
    "schedule": {"ui", "app", "cli", "entry"},
    "cli": {"ui", "app", "entry"},
    "ui": {"hw", "app", "service", "schedule", "cli", "entry"},
    "app": {"entry"},
    "entry": set(),
}

# >60 行登记例外：状态机/唤醒阶梯/UI 复合控件，拆开反而破坏可读性。
# 新增条目必须写理由；能用 helper 拆掉的不要往这里塞。
_FUNC_EXCEPTIONS = {
    "hw/topology.py::topology_off": "topology 状态机时序壳（P4 已拆 helper）",
    "hw/topology.py::topology_plan_off": "topology 状态机时序壳（P4 已拆 helper）",
    "hw/topology.py::topology_on": "topology 状态机时序壳（P4 已拆 helper）",
    "hw/topology.py::topology_plan_on": "topology 状态机时序壳（P4 已拆 helper）",
    "hw/ccd.py::screen_verdict": "亮灭判定多分支状态机",
    "hw/power.py::_wake_one": "四级唤醒阶梯，顺序即契约",
    "ui/theme.py::apply_dark_theme": "ttk 主题批量配置，按控件类分块后仍长",
    "ui/panels.py::MonitorPanel.rebuild": "显示器行重建，按区块拆会丢上下文",
    "ui/panels.py::SchedulePanel.__init__": "定时面板一次装配，拆开易漏控件",
    "app/layout.py::restore_default_layout": "CCD 恢复时序 + 错误分支，待后续专项拆分",
}


def _source_paths():
    files = []
    for sub in SOURCE_DIRS:
        files.extend(sorted((ROOT / sub).glob("*.py")))
    files.extend(SOURCE_FILES)
    return [p for p in files if p.exists()]


def _layer_of_path(path):
    rel = path.relative_to(ROOT).as_posix()
    for layer in SOURCE_DIRS:
        if rel.startswith(layer + "/"):
            return layer
    return {
        "service.py": "service", "schedule.py": "schedule",
        "schedule_report.py": "schedule", "cli.py": "cli",
        "monitor_brightness.py": "entry",
    }.get(rel)


def _layer_of_module(module):
    head = module.split(".")[0]
    if head == "tkinter":
        return "tkinter"
    return {
        "core": "core", "domain": "domain", "hw": "hw", "ui": "ui",
        "app": "app", "service": "service", "schedule": "schedule",
        "schedule_report": "schedule", "cli": "cli",
        "monitor_brightness": "entry",
    }.get(head)


def _iter_functions(nodes, prefix=""):
    for node in nodes:
        if isinstance(node, ast.ClassDef):
            yield from _iter_functions(node.body, prefix + node.name + ".")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield prefix + node.name, node
            yield from _iter_functions(node.body, prefix + node.name + ".")


class ArchitectureContractTest(unittest.TestCase):
    def test_layering_has_no_reverse_imports(self):
        violations = []
        for path in _source_paths():
            src_layer = _layer_of_path(path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    dst = _layer_of_module(module)
                    if dst and dst in _FORBIDDEN[src_layer]:
                        violations.append("%s:%d %s -> %s" % (
                            path.relative_to(ROOT).as_posix(), node.lineno,
                            src_layer, dst))
        self.assertEqual(violations, [], "反向/越层 import：\n" + "\n".join(violations))

    def test_no_source_file_over_700_lines(self):
        bad = []
        for path in _source_paths():
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > 700:
                bad.append("%s: %d 行" % (path.relative_to(ROOT).as_posix(), lines))
        self.assertEqual(bad, [], "文件超过 700 行：\n" + "\n".join(bad))

    def test_function_length_ratchet(self):
        over, warn = [], []
        for path in _source_paths():
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for name, node in _iter_functions(tree.body):
                length = node.end_lineno - node.lineno + 1
                key = "%s::%s" % (rel, name)
                if length > 60:
                    if key not in _FUNC_EXCEPTIONS:
                        over.append("%s: %d 行" % (key, length))
                elif length > 40:
                    warn.append("%s: %d 行" % (key, length))
        self.assertEqual(over, [], "函数超过 60 行且未登记例外：\n" + "\n".join(over))
        if warn:
            warnings.warn("函数进入 41–60 行警戒区（不失败）：\n" + "\n".join(warn))


if __name__ == "__main__":
    unittest.main()
