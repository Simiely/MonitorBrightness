# -*- coding: utf-8 -*-
"""测试入口聚合（P1-8）。实际用例已按域拆到 tests/cases_*.py。

保留本文件是为了 `py -m unittest tests.test_core` 仍然可用；
不要把 cases 文件命名成 test_*.py，否则 discover 会重复收集。
"""

from tests.cases_config import *  # noqa: F401,F403
from tests.cases_schedule import *  # noqa: F401,F403
from tests.cases_screen import *  # noqa: F401,F403
from tests.cases_ui import *  # noqa: F401,F403
from tests.cases_topology import *  # noqa: F401,F403
from tests.cases_regression import *  # noqa: F401,F403
