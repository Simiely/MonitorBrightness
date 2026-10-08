# CHANGELOG

本文件记录项目所有值得注意的变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.4.0] - 2026-10-08

### 新增（深色弹窗）

- **所有弹出通知改为深色模式**（用户反馈：启用定时后弹出的通知不是深色）：
  自定义 `DarkBox` 模态对话框全面替代 `messagebox` —— 深色背景/文字、主题蓝"确定"按钮、
  居中于主窗口、支持 Enter/ESC 关闭，info/warn/error 三种类型各配图标与颜色
- 原因：messagebox 是 Windows 原生对话框，不吃 ttk 深色主题；
  实测 `SetPreferredAppMode(ForceDark)` 只能暗标题栏、内容区仍为浅色（A/B 截图：
  标题栏暗但内容均值 184 vs 基线 196），故放弃该未公开 API 路线，改用完全可控的自绘对话框
- 模态模式与 CustomTkinter 对话框一致（Toplevel + grab_set + wait_window）
- 移除无效果的 `_force_dark_dialogs`（未公开序数 API，避免系统更新后失效的隐患）

## [1.3.2] - 2026-10-08

### 修复

- **任务计划状态显示「读取失败」**（用户实机抓到）：S5 新增的 `_next_run_text` 里
  `(now or datetime.now()).hour * 60 + now.minute` —— `.minute` 读的还是原始 `now`（None）
  → AttributeError；而 `query_task_status_local` 的解析段没有 try 保护，异常炸出去
  被 job 兜底接住 → 返回空串 → 两条任务都显示「读取失败」。
  现改为 `now_dt = now if now is not None else datetime.now()`（与 `_minutes` 同款写法），
  并补了真实路径测试（query_task_status 本机实测返回 Schedule/CatchUp 两行）。
  此前 96 项自测没抓到的原因：任务状态查询这条真实路径从未被任何测试调用过。

## [1.3.1] - 2026-10-08

### 修复

- **点「保存并启用定时」报 NameError: register_tasks is not defined**（用户实机抓到）：
  S5 施工按锚点整段替换时，把夹在 `build_register_script` 与 `build_unregister_script`
  之间的 `register_tasks` 一并删掉了；96 项自测当时没抓到 —— 因为没有任何测试调用它。
  现已恢复，并加了两道守卫：
  1. **模块完整性检查**（新增自测）：迷你"未定义名"分析 —— 全模块每个函数体内引用的、
     既非局部又非模块级/builtin 的名字必须为 0（含反向对照：在内存里删掉
     register_tasks 后检查器必须报出 `App._save_schedule_inner -> register_tasks`）
  2. `register_tasks` / `unregister_tasks` 存在性断言

## [1.3.0] - 2026-10-08

### 新增（S5：多时间段）

- **时间段数量不再固定为两档**：定时区改为动态行，点「+ 添加时间段」随时追加
  （时间 + 亮度），每行带「删除」按钮（至少保留一档）
- **调度语义（N 档）**：按时间排序，取「time <= 当前时刻」的最后一档生效；
   早于全部档位时取最后一档（跨午夜回绕）。同一时刻只能有一档
- **计划任务合并**：Day/Night 两条任务合并为一条 `MonitorBrightness_Schedule`，
  挂 N 个 Daily 触发器（一条任务多触发器是任务计划程序标准能力）；
  旧任务名注册时自动清理；CatchUp 登录校准任务保留
- **配置 v3**：`slots` 列表取代 day_time/day_level/night_time/night_level 四键，
  旧配置自动迁移（v1/v2 → v3 不丢设置）；「测试当前档」按钮按当前时刻判定下发
- 实测：96 项自测（含 N 档回绕/单档/三档/乱序/迁移）+ 表单交互 9 项 + 下发 4 项全过

## [1.2.3] - 2026-10-08

### 变更（S3 视图/协调者分离，行为不变）

- `App`（517 行 / 34 方法）拆为三个 `ttk.Frame` 子类 + 瘦身后的协调者：
  - `MonitorPanel`（64 行 / 5 方法）：勾选行、全选/全不选、就地改文字
  - `BrightnessPanel`（44 行 / 5 方法）：滑块、"当前亮度"行、应用/读取按钮
  - `SchedulePanel`（101 行 / 9 方法）：启用开关、两档表单与校验、测试/保存/取消、任务状态
  - `App`（**304 行 / 24 方法**，-41%）只保留服务调用、异步编排与弹窗，不再出现建控件细节
- 实测：91 项自测 + 勾选/读取功能 5/5 通过；CC>10 为 0；6 处耗时调用全部后台化
- 顺带：`load_config` 拆出 `_load_model_cache`（CC 11 → 7）
## [1.2.2] - 2026-10-08

### 变更

- **计划任务改指 v1.2.x 的 exe**（此前指向 v1.1.0.exe，工作区一旦清理任务会静默失效）：
  用应用自己的 `build_register_script` 重新注册三条任务（用户真实配置不变），
  实测 `CATCHUP=OK / StartWhenAvailable=True / CatchUpTask=Ready / REGISTER_OK`，
  三个任务 XML 的 Command 均指向 v1.2.x exe
- 文档：AGENTS 基线更新到 v1.2.x、REFACTOR_PLAN 标记 S1/S2/S4 完成（S3 待做）
- 代码行为与 1.2.1 一致，本次升号对应上述文档与任务指向变化
- **规则固化**：今后任何修改（含文档）→ 版本号 +1 → 重新打包 → 重注册任务 → 交付

## [1.2.1] - 2026-10-08

### 变更

- **型号名读取提速**：实测读亮度只要 **~127 ms**，慢的是型号名 —— DDC 能力串 **~3.1 s/台**，
  原先串行读 2 台要 **6.2 s**。改为：
  1. 每台一个线程**并行**读（2 台 ~3.1 s）；
  2. 型号名**持久缓存**进 config.json 的 `models` 字典（按显示器序号），
     **第二次启动起 0 ms 秒出**，DDC 只在缓存缺失时才读
     （换插口导致序号变化时会重新读一次再缓存）。

## [1.2.0] - 2026-10-08

### 变更（架构重构，行为不变）

- **S1 统一异步入口** `run_bg(job, on_done, busy=False)`：此前 6 个耗时动作各自手写
  「建线程 + 投队列 + 写 `_handle_*` + 注册分发表」；现在只剩一个入口，
  新增异步动作只需写一个回调。job 抛出的异常也会回主线程交给回调，不再后台静默吞掉。
- **S2 设置单一来源**：新增 `SETTINGS` 声明表（key/kind/default）与 `SCHEDULE_ROWS`
  （时间+亮度+任务名+标题 绑成一行），默认配置、逐键校验 `coerce_setting`、表单变量、
  两行控件、测试按钮、读表单、注册脚本参数、档位判定全部由表派生。
  实测触点：`day_time`/`day_level`/`night_time`/`night_level` 从 8~9 处降到 **2 处**；
  新增一档只需 `SCHEDULE_ROWS` 加一行 + `SETTINGS` 加两个键。
- 性能保持：`App(root)` 构造+首帧 **~70 ms**（重构前 2639 ms）；6 处耗时调用全部后台化。
- **型号名读取提速**（用户反馈"亮度读取感觉也慢"）：实测读亮度只要 **~127 ms**，
  慢的是型号名 —— DDC 能力串 **~3.1 s/台**，原先串行读 2 台要 **6.2 s**。
  改为①每台一个线程**并行**读（2 台 ~3.1 s）；②型号名**持久缓存**进 config.json，
  第二次启动起 **0 ms** 秒出，DDC 只在缓存缺失时才读（按显示器序号缓存，
  换插口导致序号变化时会重新读一次再缓存）。

### 已知

- `App` 类 517 行 / 34 方法，仍超 Fowler 大类阈值；下一阶段（S3）把三个分区抽成
  `ttk.Frame` 子类，计划见 `REFACTOR_PLAN.md`。

## [1.1.0] - 2026-10-08

### 新增

- **深色主题**：ttk `clam` 主题自定义配色 + DWM 沉浸式深色标题栏，界面无白色区域残留
- **显示器勾选列表**：**每台显示器前面一个勾选框**（勾选态清晰、支持禁用态），
  勾上哪几台就一起下发同一亮度，不用再按 Ctrl/Shift 做多选；
  保留「刷新 / 全选 / 全不选」按钮
- 显示器条目显示**型号 · 分辨率 · 主/副屏 · 设备名**（如 `Legion R27qe Gen2 · 2560×1440 · 主显示器 · DISPLAY1`），
  同型号屏幕也能区分
- 「当前亮度」异步读取，界面不卡死；新增「读取当前亮度」按钮
- 新增 CLI：`--apply-schedule`（按当前时间判定档位下发）、`--list`、`--selftest`

### 修复

- **界面底部「任务计划状态」一直显示「读取失败」**：`query_task_status` 用 `'; '.join`
  拼接 PowerShell 语句，把分号插到了 `}` 和 `else` 之间 →
  PowerShell **语法错误**（实测报「无法将"else"项识别为 cmdlet」）→ 整段解析失败、
  退出码非 0 → 界面只能显示「读取失败」。
  现统一改用换行拼接的 `_ps_script()`（register / unregister / query 三处），
  并只认三个已知任务名，避免 PowerShell 的错误文本混进状态列表被渲成假条目。
- **「登录后自动校准」任务注册不出来**（实机抓到的 bug）：`New-ScheduledTaskTrigger -AtLogOn`
  不带 `-User` 会被判成"任意用户登录"触发，非管理员注册直接报「拒绝访问」；
  又因为脚本带 `$ErrorActionPreference='Stop'`，整段在注册 logon 任务那一步中断，
  白/黑两条日任务之后的所有语句（含 `StartWhenAvailable` 回读）都没执行。
  现改为 `-AtLogOn -User $me`，并把这条单独包 `try/catch`：失败也不拖垮其余任务，
  原因通过 `CATCHUP=FAIL` 带回界面提示。
- **点「保存并启用定时」无声无息**：`save_schedule` 里引用了不存在的属性
  `self.catch_up_on_logon`（正确是 `self.catchup_var`），抛 `AttributeError`；
  而 `--windowed` 打包没有控制台，异常直接消失、连对话框都不弹。
  现整个回调包了异常兜底 —— 任何意外都会变成对话框，不再"点了没反应"。
- **定时在开机时间已过时不生效**（核心修复）：`schtasks.exe` 命令行**没有**「错过就尽快补跑」开关，
  v1.0.0 用它注册的任务在电脑关机 / 睡眠错过时间点后不会补跑。
  改为 PowerShell `Register-ScheduledTask` + `New-ScheduledTaskSettingsSet -StartWhenAvailable`。
- **两条任务同时被补跑会互相覆盖**：任务动作统一改为 `--apply-schedule`，
  由程序按**执行时刻**自行判定该用白天还是晚上档（带 ±2 分钟抖动容错），因此结果幂等。
- **新增「登录后自动校准」任务**（`MonitorBrightness_CatchUp`，登录后 30 秒执行），
  保证开机时间已晚于切换点时也能立刻补上，不依赖 StartWhenAvailable 单独生效。
- 源码模式下注册的任务跑不起来：v1.0.0 用 `sys.executable` 当程序路径，
  直接跑脚本时那是 `python.exe` 而非目标程序。现改为打包态用自身 exe、源码态带上脚本路径。
- 界面卡在「读取中…」：后台线程里调 `root.after` 会抛 `main thread is not in main loop`。
  改为「后台线程 → 队列 → 主线程轮询」，全程不跨线程碰 Tk 控件。
- 「当前亮度」迟迟不出：型号名（MCCS 能力串）实测约 3 秒/台，与亮度读取解耦成两个后台任务。
- 显示器名称显示成 `<Monitor object at 0x...>`：`monitorcontrol` 的 Monitor **没有** `model` 属性，
  旧写法从未生效。现从 MCCS 能力串取型号，并配合 Win32 枚举补分辨率与主/副屏。

### 变更

- **性能：把所有耗时操作移出主线程**（用户反馈"卡顿感太强"）。修复后「双击到看见窗口」
  从 **2639 ms 降到 70 ms**：
  | 操作 | 修复前（主线程同步） | 修复后 |
  |---|---|---|
  | 启动时查任务计划状态 | 2441 ms（PowerShell 冷启动） | **0 ms**（改读本地任务 XML，~1 ms） |
  | 点「保存 / 取消定时」 | ~3000 ms 界面冻结 | 后台执行，不冻结 |
  | 点「应用到选中显示器 / 测试」 | 370 ms 界面冻结 | 后台执行，不冻结 |
  - 任务状态改走 `query_task_status_local()`：直接解析 `<SystemRoot>\System32\Tasks\<任务名>`
    的 XML（UTF-16 LE），取 Enabled / StartWhenAvailable / StartBoundary，
    「下次运行」由 StartBoundary 的本地时间推算（显示为「今天/明天 HH:MM」）；读不到才回退 PowerShell。
  - 状态区**固定 4 行**（启动即写占位），消除异步结果回来时的窗口高度跳变。
  - 型号名到达时**就地改那一行文字**，不再销毁重建整个勾选列表。
  - 耗时操作进行中禁用相关按钮，避免连点起一串后台线程。
- **结构：按量化审计结果重构**（判据见 DEVELOPMENT.md「架构健康度」）
  - `App._build` 92 行 → 拆成 `_build_monitor_group` / `_build_brightness_group` /
    `_build_schedule_group`（最长 35 行）
  - 白天/晚上两行输入合并为 `_build_time_row()`，去掉一份重复
  - 圈复杂度 >10 的方法：**4 个 → 0 个**
- **显示器改为每台一个勾选框**：勾上哪几台就一起下发同一亮度，不用再按 Ctrl/Shift 多选；
  保留「刷新 / 全选 / 全不选」
- 配置文件升级到 v2：`monitor`（单序号）→ `monitors`（序号列表）；旧配置自动迁移
- 定时档位校验：白天与晚上时间不得相同，时间格式必须是 `HH:MM`
- **界面配色重做**：去掉满屏饱和蓝，改成中性灰打底（`#1e1e1e` / `#252525` / `#333`），
  强调色只出现在「主按钮 / 勾选框选中态 / 列表选中」三处；分组标题由"蓝色粗体"改为常规字重浅灰；
  滑块改为中性灰
- **勾选框改为 ttk 官方扩展方式**：用 `style.element_create` + `style.layout`
  替换 `Checkbutton.indicator` 指示器元素（纯代码生成的图片，不引入外部资源文件）。
  ⚠️ 修正上一版的做法：原先给 `tk.Checkbutton` 设 `selectcolor=强调色`，
  导致**未勾选时方块也是蓝色的**，跟勾上分不出来（用户反馈的问题）

## [1.0.0] - 2026-09-25

### 新增

- 图形界面（tkinter）：显示器选择、亮度滑块即时调节
- 每日定时：白天 / 晚上两个时间点与亮度，一键写入 Windows 任务计划程序
- 命令行模式：`--set <index> <value>`，供任务计划静默调用
- 单文件 exe 打包（PyInstaller `--onefile --windowed`，约 14 MB）
- 配置持久化：`%APPDATA%/MonitorBrightness/config.json`
- 附 PySide6/Qt 界面变体源码（`monitor_brightness_qt.py`）

[1.0.0]: https://github.com/Simiely/MonitorBrightness/releases/tag/v1.0.0
