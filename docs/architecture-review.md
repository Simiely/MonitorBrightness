# 架构审核报告（只读留档，不实施重构）

- 日期：2026-11-04
- 对象：`monitor_brightness.py`（v1.9.3 工作区，单文件 5126 行）
- 基线：`v1.8.5`（4157 行，用户已验证可用）
- 方式：静态读取 + 只读分析工具（radon 6.0.1 / pylint 4.1.3 / ruff 0.17.3 / 自写 AST 脚本）
- 约束：本报告**不附带任何代码改动**；不触碰显示器；不覆盖 `D:\Tool\MonitorBrightness.exe`
- 相关文档：[回滚分类表](rollback-classification.md) · [CHANGELOG](../CHANGELOG.md)

> 说明：本报告回答「架构清不清、主线/支线清不清、模块化不模块化」。所有结论后跟证据编号，
> 证据在 §2–§10 的数据里。评价标准写在 §1，每条都有出处，不写"业界一般认为"。

---

## 0. 结论摘要（分级）

| 维度 | 结论 | 证据 |
|---|---|---|
| 整体架构清晰度 | **部分清晰** | E1–E5、E9、E12 |
| 主线（判定→注册→补检→熄灭） | **部分贯通**：时间判定与熄灭是单一实现；注册有 6 个入口但收敛到 1 个写者；补检是第二条会触发熄灭的路径 | E6、E7、E13 |
| 支线（CLI / GUI / 托盘 / 自启 / 计划任务） | **互有重叠**：CLI、任务、自启边界清楚；GUI 与托盘、config、DDC 边界不清 | E4、E5、E8、E10 |
| 模块化 | **低**：8 个"段"只是注释分区，不是可导入模块；0 个业务模块 | E1、E2 |
| 可测试性 | **中等**：约 54.8% 函数行数可脱离 Tk 运行；22 项单测；GUI 回调不可测 | E11、E12 |
| God Object 风险 | **高**：`App` 31 个实例属性、26 个公开方法、`__init__` 56 条语句（Pylint 阈值 7/20/50） | E9 |

一句话：**代码分区清楚、核心算法不复杂（平均圈复杂度 A/3.65），但"单文件 + App 上帝对象 + GUI 直连 DDC + 15 个 config 写者"让架构只能算部分清晰。** 重构应做，但现在不做——熄灭/唤醒路径刚回滚到用户验证过的基线，任何拆分都必须"原样搬家"，不许顺手改行为。

---

## 1. 评价标准与出处

| 编号 | 标准 | 阈值/判据 | 出处 |
|---|---|---|---|
| S1 | 单一职责（SRP） | 一个模块/类/函数只有一个变更理由 | R. C. Martin, *Clean Code* (2008) ch.10；Wikipedia: Single responsibility principle |
| S2 | 函数要小 | 超过 50 行需说明理由；超过 100 行视为超标。报告中同时列 50–100 与 >100 两档 | *Clean Code* ch.3："functions should be small… hardly ever be 20 lines long"；本报告按用户要求用 50/100 两档 |
| S3 | 圈复杂度 | A 1–5 / B 6–10 / C 11–20 / D 21–30 / E 31–40 / F 41+；Pylint 默认 `too-many-branches=12` | McCabe (1976), *A Complexity Measure*, IEEE TSE；Radon 文档 "Cyclomatic Complexity"；Pylint `R0912` |
| S4 | God Object | 一个类承担过多职责：属性/方法/语句数超阈值 | Wikipedia: God object；Pylint `R0902`(7)、`R0904`(20)、`R0915`(50) 默认阈值 |
| S5 | 分层与依赖方向 | 外层（UI/CLI）只能依赖内层（领域/设备）；内层不得反向依赖外层 | Martin, *Clean Architecture* (2017) ch.7 "The Dependency Rule"；Cockburn, Hexagonal Architecture (2005) |
| S6 | 错误处理分层 | 在能处理错误的层捕获；捕获具体异常；不得用 `except Exception` 静默吞掉 | *Clean Code* ch.7；Microsoft Learn "Best practices for exceptions"（catch specific exceptions）；Pylint `W0718` |
| S7 | 状态所有权 | 每份可变状态只有一个写者（Single Source of Truth） | Wikipedia: Single source of truth；Martin, *Clean Architecture* ch.9（状态与架构边界） |
| S8 | 可测试性 | 核心逻辑能脱离 GUI 运行；GUI 只做"谦卑对象"（Humble Object） | Fowler, "Humble Object" (2013)；*Clean Code* ch.9 |
| S9 | 模块边界 | 模块可独立导入、独立测试、独立打包；而不是靠注释分段 | 由 S1+S5+S8 推导；Python Packaging Guide（可导入模块） |

> 阈值不是真理，只是"需要解释的理由"。本报告用它们做**分级**，不做"一票否决"。

---

## 2. 代码分布：4157 → 5126 行，8 段

| 段（注释分区） | v1.8.5 行数 | v1.9.3 行数 | 变化 |
|---|---:|---:|---:|
| 一、配置读写 | 268 | 439 | +171 |
| 二、显示器枚举与亮度（DDC/CI） | 787 | 842 | +55 |
| 三、按时间判定 | 146 | 184 | +38 |
| 四、任务计划（PowerShell） | 282 | 507 | +225 |
| 五、深色主题 | 110 | 110 | 0 |
| 六、命令行 | 199 | 217 | +18 |
| 七、图形界面（tkinter） | 2091 | 2517 | +426 |
| 八、入口 | 157 | 170 | +13 |
| **合计** | **4157** | **5126** | **+969（+23.3%）** |

- **E1**：8 段只是注释横幅（L141/580/1422/1606/2113/2223/2440/4957），文件仍是**一个模块**；`import monitor_brightness` 会把 GUI、DDC、PowerShell、Tk 全部拉起来。
- **E2**：GUI 段 2517 行，占 49.1%；DDC 段 842 行，占 16.4%。UI 是最大单体，但核心设备代码并不小。
- **E3**：v1.9.0–v1.9.3 净增 969 行，其中 GUI +426、任务计划 +225、配置 +171；DDC 只 +55（熄灭/唤醒路径已回滚，增量主要是缓存 key 与注释）。

Radon 原始值：

```
LOC 5126 / LLOC 3542 / SLOC 3748 / Comments 284 (6% L, 8% S)
Average complexity: A (3.6536)
```

- **E4**：平均圈复杂度 A（3.65），说明**大多数函数并不复杂**；问题在体量与边界，不在算法难度。

---

## 3. 函数规模：没有 >100 行，但有 11 个 50–100 行

> 用户要求"超长函数清单（>50 行 / >100 行的逐个列出）"。实测 **>100 行：0 个**。

| 函数 | 行号 | 行数 | 职责 |
|---|---:|---:|---|
| `apply_dark_theme` | 2115–2202 | 88 | 一次性配置 ttk 深色样式（线性赋值，分支少） |
| `build_register_script` | 1674–1760 | 87 | 生成注册计划任务的 PowerShell 文本 |
| `App.__init__` | 3934–4011 | 78 | 主窗装配 + 定时自检/事件/托盘/热键接线 |
| `SettingsSheet.__init__` | 3822–3889 | 68 | 设置弹窗控件装配 |
| `SchedulePanel.__init__` | 3637–3699 | 63 | 定时卡片控件装配 |
| `cli_diag` | 2349–2409 | 61 | 只读诊断输出 |
| `enumerate_targets` | 688–746 | 59 | 全程序唯一显示器枚举源 |
| `_wake_one` | 1244–1297 | 54 | 单台显示器分级唤醒（**v1.8.5 冻结基线**） |
| `task_summary` | 4901–4953 | 53 | 任务原始文本 → 界面结论 |
| `main` | 5070–5122 | 53 | 参数分派 + 单实例 + 启动 |
| `load_config` | 429–479 | 51 | 读配置 + 兼容/反向恢复 |

- **E5**：11 个函数超过 50 行；其中 7 个是**装配/模板/映射**（低分支），4 个是业务（`enumerate_targets`、`_wake_one`、`task_summary`、`load_config`）。按 S2，需要理由，但不是"必须拆"。

圈复杂度 Top（Radon，D/C 级）：

| 函数 | 复杂度 | 级别 | 说明 |
|---|---:|---|---|
| `task_summary` | 30 | D | 纯文本映射，分支多但无副作用 |
| `coerce_setting` | 20 | C | 设置项类型强制，配置入口 |
| `load_config` | 18 | C | 读盘/兼容/恢复，配置入口 |
| `screen_verdict` | 16 | C | 亮灭判定（读 VCP + 空闲时间） |
| `cli_diag` | 16 | C | 诊断输出 |
| `main` | 16 | C | 参数分派 |
| `task_match_reason` | 14 | C | 任务 XML 比对原因 |
| `App._render_states` | 13 | C | 状态卡刷新 |
| `App._poll_read_queue` | 12 | C | 后台结果消费 |
| `_coerce_slots` / `catch_up_decision` / `_rebuild_report` / `App._finish_auto_checks` | 11 | C | 配置/补检/提示/自检 |

- **E6**：C/D 级 13 个，集中在"配置解析"（`coerce_setting`/`load_config`/`_coerce_slots`）与"文本映射"（`task_summary`/`task_match_reason`/`_rebuild_report`）。这两类正是重构时最容易改出静默异常的地方。

---

## 4. GUI ↔ DDC 耦合点：8 个 GUI 方法直接调设备层

| GUI 方法 | 行号 | 直接调用 |
|---|---:|---|
| `App.refresh_monitors` | 4150–4166 | `enumerate_targets`, `release_targets` |
| `App.refresh_brightness` | 4240–4258 | `get_brightness_many` |
| `App._fetch_models` | 4260–4292 | `_dxva2`, `enumerate_targets`, `release_targets` |
| `App.apply_now` | 4294–4301 | `set_brightness_many` |
| `App.apply_preset` | 4315–4325 | `set_brightness_many` |
| `App.probe_screens` | 4348–4363 | `get_brightness_many` |
| `App.screen_off` | 4420–4426 | `screen_off_cfg` |
| `App.screen_on` | 4447–4453 | `screen_on_cfg` |

- **E7**：GUI 段绕过任何"控制器/服务"层，直接调用 DDC 函数（违反 S5）；其中 `screen_off/on` 是**点击路径**，任何 GUI 卡顿都会直接放大成"点了没反应"。
- **E8**：`App._fetch_models` 仍保留 v1.9.0 的"线程自己销毁句柄"写法（与已回滚的 `screen_wake_many` 不同）；它不在唤醒路径，但属于同一类"理论 UAF 修复"，**未回滚**，见分类表。
- 反例（做得对的）：CLI 只调用核心函数（`cli_apply_schedule` → `apply_schedule`，`cli_screen` → `screen_off_cfg`），没有自己实现一套。

---

## 5. 全局可变状态清单

| 名称 | 行号 | 类型 | 写者/用途 |
|---|---:|---|---|
| `_EVENT_LOCK` | 372 | `threading.Lock` | 事件日志串行化 |
| `CONFIG_LOAD_ERROR` | 435 | `str`（`global`） | 配置读失败提示，跨函数传递 |
| `_DXVA2_READY` | 635 | `bool`（`global`） | dxva2 懒加载标志 |
| `_EDID_CACHE` | 786 | `dict` | EDID → 短哈希 |
| `_CAPS_CACHE` | 843 | `dict` | 能力串缓存（含落盘） |
| `_MAX_LUM` | 934 | `dict` | 亮度上限缓存 |
| `_TICKBOX_IMAGES` | 2451 | `list` | Tk 图片引用保活 |
| `_AA_CACHE` / `_AA_REFS` | 2771–2772 | `dict` / `list` | 抗锯齿位图缓存 |
| `_mutex_handle` | 5010 | 句柄（`global`） | 单实例互斥体 |
| `WAKE_STAGE_LABEL` / `OFF_METHOD_LABEL` / `POWER_LABEL` / `INPUT_LABEL` | 1177/157/609/612 | 常量字典 | 只读，列出以便区分 |

- **E9**：真正可变的全局有 9 处；其中 3 个是 `global` 语句（Pylint/ruff `PLW0603` 均报）。`CONFIG_LOAD_ERROR` 跨层传递是"用全局变量做返回值"，破坏 S7。
- **E10**：`_CAPS_CACHE` / `_MAX_LUM` / `_EDID_CACHE` 同时被 GUI、CLI、DDC 读写；缓存 key 在 v1.9.0 改成 `DEV#EDID`（用户点名保留），三类调用者共享同一份 dict，没有锁。单线程 GUI 下没事，补检/唤醒线程并发时是隐性竞争。

---

## 6. config 的写者：19 个调用点，15 个函数

```
load_config            apply_slot             App.apply_setting(5 处)
App.apply_off_method   App._apply_models      App._render_states
App._finish_screen_off App._finish_screen_on  App._tray_toggle_schedule
App.on_slots_changed   App._save_schedule_inner App.apply_schedule
App._finish_register   App.cancel_schedule    cli_screen
```

- **E11**：15 个函数直接 `save_config`，**没有任何单一写者**（违反 S7）。其中 12 个在 GUI 段。
- 典型风险：GUI 用旧内存值覆盖计划任务刚写的 `last_fired`；v1.9.0 用 `_read_disk_last_fired` + `_fired_newer` 做了补丁式合并，但写者数量没降。
- **E12**：v1.9.3 的反向恢复让 `load_config` 也成为写者（只在 `system → ddc` 时写一次并记日志）。这是本次回滚的**已知代价**，报告如实记录；它不扩大日常写入面，但确实破坏了"读函数不写盘"的直觉。

---

## 7. 可测试性

| 指标 | 数值 | 说明 |
|---|---:|---|
| 可脱离 Tk 的函数行数 | 2361 / 4308 | 54.8%（不含 UI 类、不引用 tk 符号） |
| 函数总数 | 327 | 含 100+ 个 Tk 控件方法 |
| 单测 | 22 项 | `python -m unittest discover -s tests`，不碰硬件 |
| 覆盖核心纯函数 | 是 | `parse_hhmm`、`active_slot`、`catch_up_decision`、`task_summary`、`coerce_*`、`build_register_script` 等 |
| GUI 回调 | 不可测 | 依赖 Tk 事件循环；v1.9.1 起异常写 `error.log` + 状态栏 |

- **E13**：核心判定/配置/任务文本约一半可脱离 GUI 测试；`apply_slot`/`screen_off_cfg` 因直接碰 DDC，只能用打桩测调用序列（本轮回滚验证就是这么做的）。
- 按 S8，GUI 应是"谦卑对象"：只收集输入、展示结果；现在 `App` 同时做布局、状态、DDC 调用、config 写盘、任务注册，**不是谦卑对象**。

---

## 8. 主线：判定 → 注册 → 补检 → 熄灭

### 8.1 贯通的部分

1. **时间判定单一实现**：`parse_hhmm` → `active_slot` / `active_slot_dict` → `apply_slot`。GUI、CLI、补检都走这一条。
2. **熄灭单一实现**：`App.screen_off` / `cli_screen` / `apply_slot` → `screen_off_cfg` → `screen_off_many`（ddc）或 `screen_off_system`（system）。没有第二套熄灭代码。
3. **任务注册单一落地**：所有入口最终都调 `register_tasks`；`ensure_task_registration` 负责"先比对 XML，再决定是否重建"。

- **E14**：这三条主线的**核心实现没有重复**，这是架构里最扎实的部分。

### 8.2 分叉/重叠的部分

| 入口 | 函数 | 行号 |
|---|---|---:|
| 启动自检 | `App._start_auto_checks` → `ensure_task_registration` | 4640/4652 |
| 无界面自愈 | `cli_ensure_schedule` | 2412 |
| 时间段增删改（500ms 防抖） | `App.on_slots_changed` → `_register_after_edit` | 4709/4728 |
| 开关「启用每日定时」 | `App._save_schedule_inner` | 4756 |
| 「应用」按钮 | `App.apply_schedule` | 4777 |
| 托盘切换定时 | `App._tray_toggle_schedule` | 4572 |

- **E15**：6 个入口都写同一任务，但各自带一套"保存 config + 重建 + 提示"的顺序；其中 `App.apply_schedule` 与模块级 `apply_schedule` **同名不同义**（前者重建任务，后者套用当前档位），是命名层面的主线歧义。
- **E16**：补检是**第二条会触发熄灭的路径**：`App._catch_up_worker` → `catch_up_decision` → `apply_slot` → `screen_off_cfg`。它不在"点击熄灭"主线里，但会在启动时自动执行今天错过的那一档；上限 `CATCH_UP_LIMIT_MIN = 720`（12 小时）。**这是"开机擅自熄屏"的唯一代码路径**，见分类表第 3 项。

结论：主线**部分贯通**——核心实现单一，但入口多、命名有歧义、补检是旁路。

---

## 9. 支线边界

| 支线 | 行数 | 边界评价 | 证据 |
|---|---:|---|---|
| CLI | 217 | **清楚**：只做参数解析 + 调核心函数；`cli_screen` 直接写 config 是唯一越界 | E7 |
| 计划任务 | 507 | **清楚但脆**：PowerShell 字符串模板集中在一个函数，改动风险高 | E6 |
| 自启 | ~30 | **清楚**：注册表读写薄封装 | — |
| 托盘 | ~150 | **基本清楚**：调 App 方法；`_tray_toggle_schedule` 自己写 config | E11 |
| GUI | 2517 | **不清**：布局、状态、DDC、config、任务注册混在 `App` | E7/E9/E11 |

- **E17**：`App` 的 31 个属性里同时有 UI 控件、设备快照、定时状态、线程队列、托盘句柄；`__init__` 56 条语句把六类依赖一次装完。按 S4 是典型 God Object。
- **E18**：`schedule` 段只有 184 行，却承担"判定 + 补检"两个概念；`task` 段 507 行承担"生成脚本 + 查询状态 + 解析 XML + 提示文案"。按 S1 都应再分。

---

## 10. 工具实测原始值

### 10.1 Radon

```
Average complexity: A (3.6536144578313254)
MI: monitor_brightness.py - C (0.00)
LOC 5126 / LLOC 3542 / SLOC 3748
```

- MI 0.00 是**体量惩罚**（单文件 5126 行），不是"每个函数都烂"；结合平均 A 看，别误读。

### 10.2 Pylint（只启用与架构相关的检查）

| 消息 | 次数 | 含义 |
|---|---:|---|
| `W0718 broad-exception-caught` | **81** | 捕获 `Exception`；多数是 GUI/DDC 兜底 |
| `R0902 too-many-instance-attributes` | 4 | `App` 31/7、`SchedulePanel` 19/7、`StatusCard` 16/7、`Button` 11/7 |
| `R0914 too-many-locals` | 3 | `task_summary` 24/15、`SettingsSheet.__init__` 16/15 等 |
| `R0912 too-many-branches` | 2 | `coerce_setting` 14/12、`main` 13/12 |
| `R0904 too-many-public-methods` | 1 | `App` 26/20 |
| `R0915 too-many-statements` | 1 | `App.__init__` 56/50 |
| **合计** | **92** | — |

### 10.3 Ruff（C901 / PLR0912 / PLR0915 / E722 / PLW0603）

```
coerce_setting C901 (15>10) + PLR0912 (14>12)
load_config C901 (12>10)
task_summary C901 (12>10)
main C901 (14>10) + PLR0912 (13>12)
App.__init__ PLR0915 (55>50)
PLW0603 × 3：CONFIG_LOAD_ERROR / _DXVA2_READY / _mutex_handle
Found 11 errors.
```

- **E19**：工具与人工结论一致——复杂度不是主要问题，**体量、全局状态、异常兜底**是。81 次 `W0718` 不代表 81 个 bug，但说明错误处理没有分层（S6）。

---

## 11. 重构方案（按收益/风险排序；**现在不动手**）

> 所有方案都要求：**原样搬家、不改行为**；一次只搬一个模块；每步跑 22 项单测 + `--diag` + 启动 smoke test。
> 熄灭/唤醒路径（`_broadcast_monitor_power` … `screen_wake_many`）在 v1.9.3 已冻结，**不得在重构中"顺手优化"**。

| 顺序 | 方案 | 收益 | 迁移代价 | 回归风险 | 改打包？ |
|---|---|---|---|---|---|
| 1 | `config_store.py`：schema + load/save + `last_fired` 合并，15 个写者 → 1 个 `update()` | 高（消除覆盖写、可测） | 中（改 19 个调用点） | 中（`last_fired`/caps 落盘） | 否 |
| 2 | `ddc/`：把 §二 842 行 + 懒加载 + 3 个缓存整体搬走，GUI 只调 `ddc.xxx` | 高（隔离设备层） | 中（保留全局缓存语义） | 中（PyInstaller 隐式导入；句柄生命周期） | 是（hiddenimports 检查） |
| 3 | `schedule_core.py`：`parse_hhmm`/`active_slot`/`catch_up_decision`/`apply_slot` | 高（纯逻辑，已可测） | 低（纯函数搬家） | 低 | 否 |
| 4 | `tasks.py`：PowerShell 模板 + XML 比对 + 运行时查询 | 中（507 行独立） | 中（字符串模板原样搬） | 中高（引号/编码） | 否 |
| 5 | `ui/`：`App` 拆成 panels + controller | 中（降 God Object） | 高（2517 行） | 高（Tk 布局/事件） | 是（资源路径） |
| 6 | 只加 `App -> controller -> ddc` 一层薄封装（不搬 DDC） | 低 | 低 | 低 | 否 |

**"拆了会不会再撞静默异常？"**
- 会新增两类风险：**ImportError/打包缺模块**（启动即崩，比静默异常更明显）和**跨模块异常被新边界吞掉**。
- 已有护栏：`report_callback_exception` → `error.log` + 状态栏（v1.9.1）。
- 必须补的护栏：每个新模块被 `tests/test_core.py` 直接 import；打包后跑 `--diag`、`--selftest`、启动 3 秒 smoke；GUI 回调仍统一走 `_report_tk_error`。
- 结论：**拆模块本身不会制造 v1.9.1 那种静默 TypeError；真正的风险是打包与导入。** 先做 1/3（低风险），再做 2/4，`ui/` 最后。

---

## 12. 明确不改的地方（看着乱，但改了风险 > 收益）

| 位置 | 现状 | 不改理由 |
|---|---|---|
| 熄灭/唤醒路径（§二，1201–1421） | 刚回滚到 v1.8.5，函数与基线逐字节一致 | 用户已验证；任何"优化"都是 v1.9.0 错误的重复 |
| `apply_dark_theme` 88 行 | 线性样式赋值 | 拆成函数只增加跳转，不降复杂度（Radon A4） |
| `build_register_script` 87 行 | PowerShell 文本模板 | 拆字符串片段会增加引号/转义错误面；应加"生成脚本黄金测试"而非重写 |
| `task_summary` 复杂度 D(30) | 纯文本映射 | 无副作用、无 IO；改成表驱动要配一整套测试，收益低于风险 |
| `App.__init__` 78 行 | 一次性装配 | 拆 `_build_*` 只是把 56 条语句分到 6 个方法，行为风险不变、可读性提升有限 |
| `_wake_one` 54 行 | v1.8.5 唤醒阶梯 | 冻结基线，禁止动 |
| `_CAPS_CACHE` / `_MAX_LUM` / `_EDID_CACHE` 全局 dict | 进程级缓存 | 收进对象要改所有调用点；当前单 GUI 线程 + 少量后台线程，收益不足 |
| GUI 7 行直调 `screen_off_cfg` | `App.screen_off` | 只加一层转发不会让 GUI 可测；真正该做的是 §11 的 `ddc` 模块，单独包一层是"为分层而分层" |
| 托盘/自启薄封装 | 直接调 App/注册表 | 边界已清楚，拆出去没有独立价值 |

---

## 13. 局限

1. 本轮**没有真机熄屏计时**（用户已明令停止屏幕操作）。熄灭路径的验证是"函数级逐字节 diff + 写操作打桩干跑"，不是物理耗时。
2. 可测试比例 54.8% 是"函数行数加权"的静态估算，不是覆盖率；`coverage` 未安装，未跑行覆盖。
3. `pylint`/`ruff` 的阈值是工具默认值，不是项目自定义规范；本报告只用它做横向比较。
4. 架构分级是判断，不是测量；所有判断都挂了证据编号，可复核、可反驳。
