# UI 重构计划（基于依赖实测）

> 前置：P0~P4 已完成（单测 178→209，`topology_off` 176→82，
> `ccd_probe_topology` 103→21，`domain/` 包已建立）。
> 本文只处理 UI 层。铁律同前：**零行为变化、每步可回退、打包必验**。

## 一、现状（实测，不是印象）

| 项 | 数值 |
|---|---|
| UI 类合计 | **2797 行**（全文 8322，占 **34%**） |
| `App` | **1333 行 / 89 方法** |
| 其余 15 个 UI 类 | 平均 82 行 / 6 方法 |
| UI 类之间的循环引用 | **0 对** |
| 业务函数碰 tkinter | **7 个**（全是 UI 基础设施，业务逻辑零污染） |
| `status_var.set` | 68 处 / 分布在 44 个方法（平均 1.5） |

**面板对 App 的依赖（`self.app.xxx`）**

| 面板 | 次数 | 评价 |
|---|---|---|
| MonitorPanel | 15 | 偏高，可降到 3~4 |
| SchedulePanel | 7 | 可接受 |
| BrightnessPanel | 2 | 很好 |
| StatusCard / SettingsSheet / HeirSheet | **0** | 完全自治 |

**结论**：UI 的问题**不是耦合深，而是两个**：
1. **`App` 是 God Object**（1333 行 89 方法），编排、状态、托盘、定时、诊断全堆一起；
2. **UI 和业务在同一个文件**，2797 行 UI 淹在 8322 行里，找代码靠搜索。

所以 UI 重构 = **搬家 + 归类 + 拆 App**，不是"解耦"（解耦已经做得不错）。

## 二、目标架构

```
ui/
  theme.py      UI 配色、字体、DPI（apply_dark_theme / apply_tk_scaling）
  graphics.py   位图合成（_aa_rounded / _aa_circle / tickbox）
  widgets.py    Label/Button/Switch/Segmented/Tooltip/Slider/TimeField/
                Card/SectionTitle/WeekdayPicker/ScrollArea
  panels.py     MonitorPanel / BrightnessPanel / SchedulePanel / StatusCard
  sheets.py     Sheet / SettingsSheet / SlotEditor / HeirSheet / DarkBox
  app.py        App 壳（≤25 方法）+ 若干 Controller
```

**依赖规则（架构守护测试强制执行）**

| 层 | 可以 import | 禁止 import |
|---|---|---|
| `ui/**` | `domain/**`、`ui/**`、标准库 | — |
| `domain/**` | 标准库 | **`ui/**`、`tkinter`** |
| `core/win32/**` | 标准库、ctypes | **`ui/**`、`domain/**`** |

## 三、分阶段

### U0 · 护栏（先做，1 步）

1. **架构守护测试**：扫描各层源码的 import，断言依赖规则。
   - 现在 `ui/` 还不存在，先让测试对"单文件"豁免；U1 起生效。
2. **UI 冒烟测试**：用 Tk 建一次真实窗口（`root.withdraw()`），
   实例化每个面板/弹层，断言不抛异常。**这条能抓住 90% 的搬家错误。**
   - 现有 `test_remember_btn_flashes_result` 已经示范了这种写法。

**验收**：故意删掉一个 import，守护测试/冒烟测试必须变红。

### U1 · 搬基础设施（零依赖，风险最低）

- `ui/theme.py`：`UI` 字典、`_ui_font`、`apply_dark_theme`、`apply_tk_scaling`、
  `_no_window_flags`
- `ui/graphics.py`：`_aa_rounded`、`_aa_circle`、`_tickbox_image`、
  `install_dark_tickbox`、`_AA_CACHE`、`_AA_REFS`

**主文件保留 `from ui.theme import *` 式重新导出**（与 domain 试点同策略），
所以 2797 行 UI 代码里的引用**一行都不用改**。

**验收**：209 测试 + 冒烟窗口 + 打包 + 真机开窗。

### U2 · 搬控件（8 个，互相有引用）

顺序按依赖：`Label` → `Card`/`SectionTitle` → `Button` → `Switch`/`Segmented`/
`Slider`/`TimeField`/`WeekdayPicker` → `Tooltip` → `ScrollArea`。

**风险**：`_SlotRow(Button)`、`ScrolledPanel` 这类继承关系，
搬的时候父类必须先在新模块里定义好 → 按上面的顺序搬。

**验收**：同上 + 设置页/定时面板手工点一遍。

### U3 · 搬面板（依赖 App，最需要小心）

`MonitorPanel`（15 处 `self.app.`）、`BrightnessPanel`、`SchedulePanel`、`StatusCard`。

**做法**：先原样搬（仍持 `self.app`），**不改依赖**。
搬完再单独一步把 `MonitorPanel` 的 15 处降成回调注入：

```python
MonitorPanel(parent, cfg_getter=..., on_off=..., on_on=..., on_refresh=...)
```

**为什么要分两步**：同时"搬家 + 改依赖"出问题时无法判断是哪一步的锅。

### U4 · 搬弹层

`Sheet` → `SlotEditor`/`HeirSheet`/`SettingsSheet`、`DarkBox`。

**注意**：`Sheet` 是基类，先搬；`SettingsSheet` 继承它，
且内部引用 `Button`/`Switch`/`Segmented`（U2 已就位）。

### U5 · 拆 App（1333 行 → 目标 ≤600）

**不拆成"服务类"**（P2 已论证：App 大部分是 UI 编排，搬走只是换个地方）。
拆成**同一文件内的职责区块 + 少量 Controller**：

| 区块 | 内容 | 估计 |
|---|---|---|
| `App.__init__` | 拆 `_init_state` / `_build_ui` / `_bind` / `_startup` | 114 → 4×25 |
| 屏幕动作 | `screen_off`/`screen_on`/`monitor_off`/`monitor_on`/`_finish_*` | ~200 |
| 定时 | `save_schedule`/`cancel_schedule`/`on_slots_changed`/`_set_schedule_enabled` | ~150 |
| 设置/托盘 | `apply_setting`/`apply_autostart`/`_create_tray`/`_tray_*` | ~180 |
| 诊断 | `open_log_dir`/`export_diag`/`_refresh_events` | ~80 |

抽成 `ui/controllers/{screen,schedule,settings,diag}.py`，
每个 Controller 持 `app` 引用（与面板同模式），App 只做转发。

**验收**：App 方法数 ≤35、最大方法 ≤60 行（审计脚本复测）。

## 四、风险与止损

| 风险 | 概率 | 止损 |
|---|---|---|
| 搬家时漏改 import → 运行时报 NameError | 高 | `py_compile` + 209 测试 + Tk 冒烟；每搬一个模块立刻跑 |
| 循环 import（theme ↔ widgets ↔ panels） | 中 | 严格按 U1→U2→U3 顺序；守护测试抓 |
| PyInstaller 漏收 `ui` 包 | 中 | 每次打包后跑 `exe --list` + 开窗截图 |
| 视觉回归（字体/间距） | 低 | 搬家不改属性；手工对照截图 |
| UI 测试缺失导致搬家出错无感 | **高** | **U0 先补冒烟测试**；这是前置条件 |

## 五、顺序建议

```
U0 护栏 → U1 基础设施 → U2 控件 → U3 面板 → U4 弹层 → U5 拆 App
```

每步一个 commit，出问题只回退一步。U5 是最后一步，因为 App 依赖所有面板。

## 六、与已完成工作的衔接

| 已完成 | 对 UI 重构的作用 |
|---|---|
| `domain/decisions.py` | UI 搬出去后 import 它，不再依赖主文件 |
| `_topo_off_*` / `_ccd_*` helper | `App` 里的动作方法变短，U5 拆起来更容易 |
| 209 个测试 | 每步的回归底线 |
| 坑库 P-27（滚轮）/ P-20（Tooltip） | 搬家时别把 `bind_tree` / `bind_all` 的修复弄丢 |
