# DEVELOPMENT.md · 开发文档

## 项目概览

MonitorBrightness 是一个 Windows 桌面小工具，用于**外接显示器亮度的手动 / 定时调节**。

- 通过 **DDC/CI** 协议直接向显示器下发亮度指令（VCP 特性码 `0x10`），属**硬件级背光调节**，非软件滤镜，不偏色。
- 定时功能**不依赖常驻进程**：程序把「时间点 + 亮度」写入 **Windows 任务计划程序**，由系统到点调用 exe 的命令行模式执行。
- 打包为单文件 exe，绿色免安装。

## 架构说明

```
┌────────────────────────────────────┐
│  GUI (tkinter 深色主题)             │  显示器勾选列表 / 刷新 / 亮度滑块 / 定时设置
│  class App                          │  任务计划状态回显
└───────┬────────────────────┬────────┘
        │ 后台线程 + 队列     │ subprocess
┌───────▼────────────────┐   │
│  亮度控制层             │   │
│  monitorcontrol(DDC/CI)│   │
│  → dxva2.dll           │   │
│  list/set/get_..._many │   │
└────────────────────────┘   │
                             ▼
┌────────────────────────────────────┐
│  定时层                             │
│  PowerShell Register-ScheduledTask  │ → 任务计划程序
│  + -StartWhenAvailable              │   MonitorBrightness_Day
│  （v1.0.0 用的是 schtasks.exe）      │   MonitorBrightness_Night
└────────────────────────────────────┘   MonitorBrightness_CatchUp
        ▲
        │ 到点执行
┌───────┴────────────────────────────┐
│  --apply-schedule                   │ 按「当前时刻」判定白天/晚上 → 下发
│  resolve_phase() + apply_schedule() │ 目标显示器取配置里的勾选列表
└────────────────────────────────────┘

CLI 入口:    --set <all|0,1> <亮度> / --apply-schedule / --list / --selftest
配置持久化:  %APPDATA%/MonitorBrightness/config.json   （v2：monitors 为数组）
```

**单文件内的模块分区**（按注释分节）：

1. 配置读写 —— `load_config` / `save_config` / `normalize_indices` / `clamp_level`
2. 亮度控制 —— `list_monitors` / `set_brightness_many` / `get_brightness_many` /
   `enum_displays` / `monitor_model` / `monitor_label`
3. 档位判定 —— `parse_hhmm` / `resolve_phase` / `current_phase` / `apply_schedule`
4. 任务计划 —— `_ps` / `task_target` / `build_register_script` / `register_tasks` /
   `unregister_tasks` / `query_task_status`
5. 深色主题 —— `apply_dark_theme` / `apply_dark_titlebar`
6. 命令行入口 —— `cli_set` / `cli_apply_schedule` / `cli_list` / `cli_selftest`
   （`_out` / `_err` / `parse_level_arg` / `parse_index_arg`）
7. 图形界面 —— `class App`（tkinter）

## 架构健康度（2026-10-08 量化审计，不靠读代码的印象）

审计脚本：`_audit_structure.py`（静态结构）+ `_audit_perf.py`（性能与主线程阻塞）。
判据全部引自外部权威，不引自己：

| 结论类型 | 引什么 |
|---|---|
| 长函数 / 大类 | Fowler《重构》smell 目录：长函数 >20~30 行；大类 >200~300 行或 >10~15 方法 |
| 圈复杂度 | McCabe 1976 建议 10；NIST 结构化测试放宽到 15（10~15 属"复杂，中等风险"带） |
| 一处改动散改多处 | Fowler 目录 Change Preventers → **Shotgun Surgery** |
| 界面卡顿成因 | Tkinter 单线程事件循环，主线程被阻塞即冻结（官方文档/社区权威一致口径） |

### 四问直答（括号内为实测数字）

1. **主线逻辑清晰吗？——清晰。** 文件按 8 个命名区块分区，`main()` → CLI 分派 / GUI；
   核心链路「枚举显示器 → 勾选 → 下发亮度 → 注册定时 → 到点 `--apply-schedule`」可逐段追完。
   顶层 47 个函数中 **29 个（62%）是纯逻辑**（不碰 UI / 外部进程 / 设备）。
2. **支线逻辑清晰吗？——大部分清晰，有一处混线。** 配置迁移、主题安装、指示器元素、
   补跑兜底、错误回显都各自独立成函数；但 `App` 把「界面搭建」和「业务流程」
   （校验 → 存配置 → 起子进程 → 弹窗）混在同一层，`_save_schedule_inner` 就是典型。
3. **模块化吗？——逻辑上分区，物理上是单文件。**
   这是**仓库约定**（AGENTS.md「单文件交付：主脚本独立可运行，无外部资源依赖」），
   拆包会破坏"独立可运行"，所以正确方向是**逻辑模块化**而非拆文件。
4. **耦合度？——两处超阈值（见下"已知债务"）。**
   好消息：本次审计的 4 个 CC>10 已全部降到阈值内，92 行的 `_build` 已拆开。

### 本轮修掉的（都有前后数字）

| 项 | 修前 | 修后 |
|---|---|---|
| `App(root)` 构造 + 首帧 | 2639 ms | **70 ms** |
| 启动时查任务状态 | 2441 ms（PowerShell 冷启动） | **~1 ms**（读本地任务 XML） |
| 长函数 `_build` | 92 行 | 拆成 3 个，最长 35 行 |
| CC>10 的方法 | 4 个（14 / 13 / 12 / 11） | **0 个** |
| 主线程同步的耗时调用 | 6 处 | **0 处**（`_audit_perf.py` 静态自动核验） |
| 白天/晚上两行输入构建 | 两份重复代码 | 合并为 `_build_time_row()` |

### 已知债务（2026-10-08 重构后的剩余项）

1. ~~Shotgun Surgery：6 个配置键触点 ≥5 处~~ → **已修**（S2，见下表）：
   四个时间/亮度键从 8~9 处降到 **2 处**（只剩 `SETTINGS` 声明 + `SCHEDULE_ROWS` 绑定）；
   `monitors` / `catch_up_on_logon` 各剩 5 处，但每一处都是**不同层的真实消费者**
   （声明 / 迁移 / 读取 / 校验 / 展示），不再是"同一份知识抄五遍"。
2. **`App` 类实测 517 行 / 34 个方法**，超 Fowler 大类阈值（>300 行或 >15 方法）。
   下一阶段（S3）是把三个分区抽成 `ttk.Frame` 子类（MonitorPanel / BrightnessPanel /
   SchedulePanel），`App` 退化为协调者；本轮未做，计划见仓库外 `REFACTOR_PLAN.md`。

### 本轮重构（S1+S2）前后对比（全部实测）

| 指标 | 重构前 | 重构后 |
|---|---|---|
| 异步写法 | 6 份样板（建线程+put 消息+`_handle_*`+注册分发表） | **1 个入口 `run_bg(job, on_done, busy)`** |
| 加一个异步动作要改 | 3 处 | 1 处（只写 `on_done`） |
| `day_time`/`day_level`/`night_time`/`night_level` 触点 | 8~9 处 | **2 处**（只剩声明） |
| `monitors` 触点 | 9 处 | 5 处（均为不同层的消费者） |
| 白天/晚上顺序写死在业务逻辑里 | 5 处 | **1 处**（`SCHEDULE_ROWS`） |
| 圈复杂度 >10 的方法 | 0 | 0（保持） |
| 长函数 >30 行 | 7 个 | 7 个：6 个是 CC≤3 的声明式/装配代码；`_read_current_async` 34 行 CC=8（异步编排），见「审计自我约束」 |
| 新增一档（如"清晨档"）要改 | 5~6 处 | **2 处**（`SCHEDULE_ROWS` 加一行 + `SETTINGS` 加两个键） |
| `App(root)` 构造 + 首帧 | 70 ms | 70 ms（保持） |
| 自测 | 91 项通过 | 91 项通过 |

**S1（统一异步入口）**：新增 `run_bg(job, on_done, busy=False)` —— job 的返回值或异常
交给 on_done 在主线程处理；`busy=True` 期间自动禁用按钮、结束后恢复。
删掉了 `_poll_read_queue` 的 `handlers` 分发表和 5 个 `_handle_*` 样板。

**S2（设置单一来源）**：`SETTINGS` 表声明每个设置项
`{key, kind(indices|hhmm|level|flag), default}`，派生出 `default_config()` / `load_config` 逐键
`coerce_setting` 校验；`SCHEDULE_ROWS` 把「时间+亮度+任务名+标题」绑成一行，
派生出表单变量、两行控件、测试按钮、`_collect_schedule`、`build_register_script`、
`_phase_minutes` / `phase_level` / `cli_selftest`。
**验收实测**：新增一档"清晨档"要改 2 处 —— `SCHEDULE_ROWS` 加一行 + `SETTINGS` 加两个键（与上表一致）。

### 审计自我约束（可信度来源）

- 审计脚本**自己出过度量误差并已修正**：第一版用 `ast.walk` 统计圈复杂度，
  把函数内部嵌套定义的 `job_*` 闭包也算进外层，导致 `_read_current_async` 被虚高报成
  **CC=12**；改成"不进入嵌套函数/lambda"后是 **CC=8**。报告里用的是修正后的口径。
- `_audit_perf.py` 里对"耗时调用是否已后台化"的检查**一开始用子串匹配**，
  把 `unregister_tasks` 误命中成 `register_tasks`；已改为词边界正则。
  （这也是本仓库的老教训：子串断言会静默出错。）
- **第二轮审计又抓到自己的两个 bug（2026-10-08 重构后）**：
  ① "耗时调用是否后台化"的检查要求 `名字(`，而 `run_bg(unregister_tasks, ...)` 是**传函数引用**没有括号
     → `cancel_schedule` 被**漏检**（假阴性）。改成词边界 `\b名字\b` 后 6/6 全部检出。
  ② A 节的备注文案还写着"同步跑"，实际已全部后台化 —— **审计脚本的说明文字也要跟代码同步**。
- 性能数字均为本机实测中位数（`time.perf_counter`，取 3 次中位）；
  测亮度写入时**写入读到的当前值**，肉眼无变化但走真实 DDC/CI 写路径。
- 未做的事如实写在上面的"已知债务"里，不假装全覆盖。

## 关键问题与方案

### 问题：深色主题下勾选框"看不出勾没勾"

**TL;DR**：`tk.Checkbutton` 的 `selectcolor` 会让**未勾选也有底色**；clam 主题的 ttk 勾选框
选中态图形不吃 `indicatorcolor`。正解是用 ttk 官方扩展 API —— `style.element_create` +
`style.layout` 换掉 `Checkbutton.indicator` 元素。

- **现象**：未勾选和已勾选的方块看着一样（都带色），用户直接反馈"都看不出来选中没有了"。
- **排查（都有依据，不靠猜）**：
  1. 查 **Tk 官方手册**（ttk_checkbutton man page）：`TCheckbutton` 可配置项只有
     `-background` / `-foreground` / `-indicatorbackground` / **`-indicatorcolor`** /
     `-indicatormargin` / `-indicatorrelief` / `-padding`。
     ⚠️ **没有 `-indicatorforeground`** —— 一开始用的这个名字是错的，等于没设。
  2. 读**本机 Tk 自带的主题源码** `tcl/tk8.6/ttk/clamTheme.tcl`（第 73–90 行）：
     clam 对勾选框只开放 `-indicatorbackground`（默认值还是 `#ffffff` 白底），
     选中态的图形是 C 层绘制，`-indicatorcolor` 不生效。
  3. **像素实测**：把一个 `text=""` 的勾选框按屏幕矩形取像素、转成字符图，
     选中态**没有任何白色像素** —— 证实"勾号换不了颜色"，深色底下就是看不清。
  4. 换 `alt` 主题能画出正常对勾，但它的指示器底色不跟深色配置。
- **解决**：走 ttk 官方扩展机制（`ttk::style` 的 `element_create` / `layout`）：
  用两张**纯代码生成**的 `PhotoImage`（未勾=深色空框；勾上=强调色底+白勾）注册成
  `Dark.tickbox`，再把 `Checkbutton.indicator` 从 layout 里替换掉。
  这样**仍然是真正的 `ttk.Checkbutton`**，变量绑定、键盘空格、禁用态行为全部保留，
  也不需要外部图片资源（守约定：单文件交付）。
- **踩到的坑**：`style.layout` 里给 `Checkbutton.label` 传 `padding` 会报
  `TclError: Invalid -children value`；间距要用文档里的 `-indicatormargin`（左 上 右 下）。
- **预防**：新增控件视觉前，先在**放大截图/像素图**上确认状态可辨，别只看缩略图。

### 问题：界面"AI 味"重（满屏饱和蓝 + 彩色粗体小标题）

**TL;DR**：暗色 UI 的"AI 味"通常来自高饱和强调色被用到太多地方。收着用就干净了。

- **具体做法**：
  - 底色改中性灰、**不带蓝味**（`#1e1e1e` / `#252525` / `#191919` / `#333333`）；
  - 分组标题从"彩色 + 粗体"改成**常规字重的浅灰**（`#b8b8b8`）；
  - 强调色（`#2f5d8a`，低饱和钢板蓝）**只出现在三处**：主按钮、勾选框选中态、列表选中；
  - 滑块用中性灰而不是高饱和色；列表选中用低饱和蓝灰（`#33445c`）而不是亮蓝。
- **可量化的判据**：用截图统计"明显有色像素占比"，本版从满屏蓝降到 **7%**
  （全灰方案可到 0%）。比"看着是不是有点花"硬。

### 问题：重建显示器列表后「当前亮度」显示的目标集错乱（v1.1.0 早期，现已整体消失）

**TL;DR**：Tk 虚拟事件是**同步派发**的，用 `Listbox` 时重建过程会误触发一串 `<<ListboxSelect>>`。
**最终解法是把列表换成"每台一个勾选框"，这类竞争从根上没有了。**

- **踩坑过程**：早期版本用 `tk.Listbox`（`EXTENDED`）做多选，`_populate_list` 里逐条
  `delete` / `selection_set`，每步都同步触发选择事件，于是 `_read_current_async` 被
  以"当时的中间态选择集"调用。当时的补丁是重建期间 `unbind` / 重建完 `bind` 回去。
- **最终方案**：改成每台显示器一个 `ttk.Checkbutton` + `BooleanVar`。
  勾选状态是**变量**驱动的（`var.trace_add`），不再依赖"控件选择集"，
  重建时只要在 `var.set()` 前后挂起/恢复回调即可 —— 简单且没有时序问题。
- **同类教训保留**：**Tk 虚拟事件是同步派发的**。任何时候用
  `Listbox` / `Treeview` 这类"控件自带选择态"的控件，只要在代码里批量改选择，
  就要当心回调被中途触发。

### 问题：界面「任务计划状态」一直显示「读取失败」

**TL;DR**：给 PowerShell 脚本用 `'; '.join` 拼行，会在 `}` 和 `else` 之间塞进一个分号，
而 `if (...) { ... }; else { ... }` 在 PowerShell 里是**语法错误**，整段脚本解析失败。

- **现象**：任务明明注册成功了（任务 XML 都在），界面却只显示「任务计划状态：（读取失败）」。
- **根因**：`query_task_status` 把语句列表用 `"; ".join(...)` 拼成一行，其中
  `if ($t) {...}` 与 `else {...}` 是**两个独立元素**，拼出来就是
  `foreach (...) {; $t = ...; if ($t) {...}; else {...} }`。
  实测报错原文：**「无法将"else"项识别为 cmdlet、函数、脚本文件或可运行程序的名称」**。
  整段解析失败 → `powershell.exe` 退出码非 0 → `query_task_status()` 返回空串 →
  界面回落成「读取失败」。
- **修复**：新增 `_ps_script(lines)`，统一用**换行**拼接（`register` / `unregister` / `query`
  三处脚本生成器都改用它）。另外 `_refresh_task_status` 只认三个已知任务名，
  防止 PowerShell 的错误文本（stderr 已并入 stdout）里带 `=` 的行被渲成假状态。
- **为什么之前一直没发现（关键教训）**：所有截图核验脚本都把 `query_task_status` **打桩成
  返回假数据**（为了跑得快、也避开沙箱限制），于是**全项目唯一没被执行过的函数**就是它。
  打桩能提速，但**打桩掉的路径要有别的办法覆盖** —— 本次是靠用户在真实环境里发现
  「读取失败」才暴露的。以后凡是打桩，都要在自测里覆盖该函数的**返回解析**部分。
- **预防（已固化）**：自测新增一节，对四个脚本生成器的产物断言
  「不含 `; else`」「不含 `{;`」「含换行」，并配一条**反向对照**（`'; '` 拼出来的坏脚本必须被抓出）。

### 问题：「登录后自动校准」任务注册不出来 + 点保存无声无息（实机抓到）

**TL;DR**：`-AtLogOn` 必须带 `-User`，否则注册被拒（访问被拒绝）；而且 `--windowed`
没有控制台，回调里的异常会**静默消失**，用户看到的就是"点了没反应"。

- **怎么发现的**：用户在本机跑过新版并点了保存，但任务只多出 Day / Night。
  读 `C:\Windows\System32\Tasks\` 下的任务 XML（**UTF-16 LE 编码**，直接 grep 匹配不到）
  确认 `MonitorBrightness_CatchUp` 不存在 —— 而配置里 `catch_up_on_logon` 是 `true`。
- **根因 1（任务缺失）**：`New-ScheduledTaskTrigger -AtLogOn` 不带 `-User` 时，
  触发器的 `UserId` 为空 → 被当作"**任意用户登录**"→ 非管理员注册直接
  **「拒绝访问」**。实测对照：

  | 写法 | 非管理员下结果 |
  |---|---|
  | `New-ScheduledTaskTrigger -AtLogOn` | ❌ 拒绝访问 |
  | `New-ScheduledTaskTrigger -AtLogOn -User <当前用户>` | ✅ 成功 |

  又因为脚本开头是 `$ErrorActionPreference = 'Stop'` 且用 `;` 串联，
  这一句失败会让**后面所有语句都不执行** —— 包括最后那句
  `StartWhenAvailable` 回读，所以界面里连"补跑已开/未开"都读不到。
  → 改成 `-AtLogOn -User $me`（`$me = $env:USERNAME`，并留了 `WindowsIdentity` 兜底），
  且把这条**单独包 `try/catch`**：失败只输出 `CATCHUP=FAIL <原因>`，不拖垮白/黑两条。
- **根因 2（点保存没反应）**：`save_schedule` 里写了 `self.catch_up_on_logon`，
  这个属性**根本不存在**（正确的是 `self.catchup_var`）→ `AttributeError`。
  打包成 `--windowed` 后没有控制台，Tk 回调里的异常没有任何出口，用户视角就是"点了没反应"。
  → 拆成 `save_schedule()`（只做异常兜底）+ `_save_schedule_inner()`（真正逻辑），
  任何意外都变成弹窗。
- **预防（已固化）**：
  1. 自测里加了针对 `-AtLogOn -User` 的**正向断言 + 反向对照**；
  2. 脚本改为会逐条回读三个任务（`CatchUpTask=MISSING/Ready`）再输出 `REGISTER_OK`；
  3. **经验**：`--windowed` 程序里任何用户可触发的回调都必须包异常兜底，
     否则 bug 会以"点了没反应"的形式完全隐身。
- **验证方式（没用管理员、也没动用户的任务）**：把生成的脚本里三个任务名替换成
  `MBTEST_*` 实跑一遍 → 输出 `CATCHUP=OK` / `StartWhenAvailable=True` /
  `CatchUpTask=Ready` / `REGISTER_OK`，三个 XML 都真的落盘；
  再解出 `MBTEST_CatchUp` 的 XML 确认 `LogonTrigger` 里有 `<UserId>` 与 `<Delay>PT30S</Delay>`；
  最后删掉测试任务并复查用户原任务未受影响。

### 问题：定时任务在「开机时间已过」后不生效（v1.1.0 核心修复）

**TL;DR**：`schtasks.exe` 命令行没有「错过就尽快补跑」开关，必须改用 PowerShell
`Register-ScheduledTask` + `-StartWhenAvailable`；且任务要按执行时刻自行判档，才能幂等。

- **问题**：设 09:00 / 20:00 两档，若电脑在 09:00 处于关机或睡眠，醒来后亮度仍是前一档，
  到 20:00 之前都不会变。
- **根因（两层）**：
  1. Windows 任务计划**默认不补跑**错过的触发点，「Run task as soon as possible after a
     scheduled start is missed」默认是关闭的（`TaskSettings.StartWhenAvailable` 默认 `False`）。
  2. 而 **`schtasks.exe` 命令行没有对应参数** —— 只有 GUI、任务 XML、或 PowerShell 的
     `New-ScheduledTaskSettingsSet` 能设置它。v1.0.0 用 `schtasks /Create`，所以这个能力
     从一开始就拿不到。
- **解决**：
  1. 注册改走 `Register-ScheduledTask -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable ...)`。
  2. **同时**要把动作改成 `--apply-schedule`：因为开了补跑后，白天和晚上两条被错过的任务
     可能**都被补跑**，顺序不保证；若动作里写死档位，谁最后跑谁说了算，会得到错误的亮度。
     改成"程序按执行时刻自己判"之后，无论哪条跑、跑几条，结果都一致（幂等）。
  3. 再加一条**登录触发**任务（`-AtLogOn` + `Delay='PT30S'`）做兜底，保证开机时间已晚于
     切换点也能立刻补上，不必等 StartWhenAvailable 的 10 分钟排队延迟。
- **预防**：界面底部直接回显三条任务的状态与 `StartWhenAvailable` 取值，
  让"修复有没有生效"可见，而不是靠猜。
- **补充事实（官方文档）**：错过的任务会被排入队列，**默认延迟 10 分钟**才启动
  （`TaskSettings.StartWhenAvailable` 备注）。所以补跑不是"立刻"，这一点在 README 里已说明。

### 问题：显示器名称显示成 `<Monitor object at 0x...>`

**TL;DR**：`monitorcontrol` 的 `Monitor` **没有** `model` 属性，旧写法从未生效。

- **根因**：`getattr(m, "model", None)` 一直落到 `or str(m)`，于是打印对象 repr。
- **解决**：型号从 **MCCS 能力串** `get_vcp_capabilities()["model"]` 取；
  分辨率 / 主副屏 / 设备名（`\\.\DISPLAY1`）用 Win32 `EnumDisplayMonitors` 取。
  两者下标可对齐 —— `monitorcontrol` 内部也是 `EnumDisplayMonitors` + 逐个
  `GetPhysicalMonitorsFromHMONITOR`，顺序一致（已核对其源码与实测枚举结果）。
- **预防**：双屏同型号时，靠「主/副屏 + 设备名」区分，已在界面标签里带上。

### 问题：GUI 卡在「当前亮度: 读取中…」

**TL;DR**：两个原因叠加 —— 跨线程碰 Tk 控件，以及把 0.1 秒的亮度读取和 3 秒的型号读取串在一条线程里。

- **根因**：
  1. 后台线程里调 `self.root.after(0, ...)` 抛 `RuntimeError: main thread is not in main loop`
     （Tk 非线程安全）。
  2. 实测：`get_brightness_many` 只要 **0.13s**，而 `monitor_model`（MCCS 能力串）要 **3.1s/台**。
     两者串行时，亮度结果被型号名拖住。
- **解决**：改为「后台线程 → `queue.Queue` → 主线程 `after` 轮询」，
  界面只由主线程更新；并把**亮度**与**型号名**拆成两条独立后台任务，
  亮度先出、型号名稍后补（补到后只刷新列表标签）。
- **预防**：`_bg_tasks` 计数归零才停止轮询，避免空转。

### 问题：CLI 参数写错会静默把屏幕调黑

**TL;DR**：`clamp_level` 把非法输入夹成 `0`；序号非法时会退化成"全部"。CLI 必须严格校验。

- **根因**：`clamp_level("abc")` → `0`（宽恕式解析），而 `--set 0 abc` 会一路成功退出 0。
- **解决**：新增 `parse_level_arg()`（必须 0-100 整数）与 `parse_index_arg()`
  （`all` 或 `0,1`，非法返回 `None`），CLI 不合法一律退出码 `2` 并报错。
- **预防**：`--selftest` 与自测脚本对这两条做了正向与**反向对照**（喂非法值必须被拒）。

### 问题：打包出的 GUI exe 报 `No module named 'tkinter'`

**TL;DR**：打包环境 Python 是精简版（无 tkinter），换完整版 Python 打包即可。

- **问题**：PyInstaller 打包后的 exe 一运行就崩溃，提示缺少 `tkinter`。
- **根因**：所用 Python 发行版为精简版，未包含 tkinter 标准库（tcl/tk），PyInstaller 自然也就打不进去。
- **解决**：改用**完整版 Python**（官方安装包，勾选 tcl/tk）重新打包；或改用不依赖 tkinter 的 **PySide6/Qt** 写界面。
- **预防**：打包 GUI 前先自检 `python -c "import tkinter"`。

### 问题：`rm -rf dist && pyinstaller` 打包没执行

**TL;DR**：shell 把 `rm` 包装成"移入回收站"，删除被拦截返回非 0，导致 `&&` 链短路。

- **问题**：删旧目录的命令失败后，后面的打包命令根本没跑，却看不出报错。
- **根因**：环境对 `rm` 做了 safe-delete（回收站）包装，删除动作被安全策略拦截。
- **解决**：不要删旧目录，改用**全新输出目录**：`--distpath dist_new --workpath build_new --specpath build_new`。
- **预防**：构建脚本避免"先删后建"，一律输出到新目录。

### 问题：DDC/CI 枚举不到显示器

**TL;DR**：显示器 OSD 未开 DDC/CI，或信号走了非直连链路。

- **问题**：显示器列表为空 / 提示未检测到显示器。
- **根因**：DDC/CI 未启用（显示器侧开关），或信号经过 KVM / USB 转接 / 部分扩展坞被拦截。
- **解决**：在显示器物理菜单开启 DDC/CI；改用 HDMI / DP / DVI 直连。
- **预防**：把"开 DDC/CI + 直连"写进 README 的**前提**，避免用户误判程序坏了。

### 问题：定时任务到点不生效（路径类）

**TL;DR**：任务里的程序路径不对，或注册后移动了 exe。

- **问题**：到了设定时间亮度没有变化，任务计划里看得到任务。
- **根因**：
  1. 程序路径未正确引用（含空格时被错误解析）；
  2. **源码模式下用 `sys.executable` 当程序路径** —— 那是 `python.exe`，不带脚本参数跑不起来；
  3. 注册后 exe 被移动导致路径失效。
- **解决**：`task_target()` 统一处理 —— 打包态用自身 exe；源码态用 `pythonw.exe` + 带引号的脚本路径；
  exe 固定放置不再移动。
- **预防**：注册后到「任务计划程序」确认三条任务存在，并可手动「运行」验证。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。
