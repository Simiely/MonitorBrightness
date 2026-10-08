# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-10-08（v1.2.1：S1/S2 重构 + 全量后台化 + 型号名并行/缓存）
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

## 技术栈

- **Python 3.10+**（打包用**完整版**，**必须含 tkinter**）
- GUI：**tkinter**（内置，主用）/ **PySide6**（可选变体，体积大）
- 显示器控制：**`monitorcontrol`**（Windows 走 DDC/CI，VCP 特性码 `0x10` 亮度）
- 打包：**PyInstaller** `--onefile --windowed`
- 定时：Windows **任务计划程序**，v1.1.0 起改用 **PowerShell `Register-ScheduledTask`**
  （**不再用 `schtasks.exe`**，原因见下方关键坑）

## 关键坑（越具体越好）

- **拼 PowerShell 脚本一律用换行，不能 `'; '.join`** → `if (...) {...}; else {...}` / `{;`
  都是**语法错误**（实测：「无法将"else"项识别为 cmdlet」），整段解析失败、退出码非 0。
  统一走 `_ps_script(lines)`。**这是"任务计划状态：读取失败"的根因。**
- **给 `--windowed` 程序做截图核验时，不要把待验函数打桩掉** → 本项目就因此让
  `query_task_status` 成了"全项目唯一没被执行过的函数"，bug 一直藏着。
  打桩能提速，但被打桩的路径必须有别的覆盖手段（自测覆盖它的返回解析，或真机验证）。
- **别在主线程做耗时的事** → Tkinter 的事件循环与渲染共用主线程，被阻塞多久界面就冻多久。
  本项目实测的耗时项：**PowerShell 冷启动 ~2.7s**、写 DDC/CI ~370ms（两台）、读 ~124ms。
  这些一律走「后台线程 → `queue` → 主线程 `_poll_read_queue` 轮询」，
  并且**耗时期间禁用相关按钮**（防连点起一串线程）。
  改完用 `_audit_perf.py` 的静态检查复核：「凡调用耗时 API 的方法必须同时 `_bg_start()`」。
- **查任务计划状态优先读本地 XML，别为读状态启动 PowerShell** →
  `C:\Windows\System32\Tasks\<任务名>`（UTF-16 LE）里有 Enabled / StartWhenAvailable /
  StartBoundary，够用且是毫秒级；PowerShell 只留作兜底。
- **异步结果会撑大窗口 → 状态区固定行数** → 启动就写占位行，否则结果回来时窗口高度会跳。
- **审计/度量脚本自己也要校验口径** → 本项目静态分析第一版用 `ast.walk` 把闭包算进外层函数，
  圈复杂度虚高；性能审计第一版用子串匹配，把 `unregister_tasks` 误命中 `register_tasks`。
  **写度量先想清"这个数字的语义边界"**，并做反向对照。
- **`-AtLogOn` 必须带 `-User`** → `New-ScheduledTaskTrigger -AtLogOn` 不带 `-User` 会被判成
  "任意用户登录"触发，**非管理员注册直接「拒绝访问」**。用 `-AtLogOn -User $env:USERNAME`。
  且**每条 Register-ScheduledTask 单独 try/catch**：开头 `$ErrorActionPreference='Stop'`
  会让一次失败把后面所有语句（含回读校验）全部跳过。
- **`--windowed` 里任何用户可触发的回调都要包异常兜底** → 没有控制台，Tk 回调抛异常会
  **完全静默**，用户视角就是"点了没反应"（本项目真踩过：`self.catch_up_on_logon` 拼错属性名）。
- **读任务 XML 会用 UTF-16 LE** → `C:\Windows\System32\Tasks\<任务名>` 是 UTF-16 LE，
  用 ASCII 模式 grep 永远匹配不到；要先按编码解出来。该目录**列表被权限挡**，但直接按文件名访问可以。
- **`schtasks.exe` 没有「错过就补跑」开关** → 用它注册的每日任务，电脑关机/睡眠错过时间点后
  **永远不补跑**。必须用 `Register-ScheduledTask` + `New-ScheduledTaskSettingsSet -StartWhenAvailable`。
  这是 v1.0.0 定时不生效的根因。
- **StartWhenAvailable 会让"错过的那几条"都补跑** → 补跑顺序不保证。所以任务动作不能写死档位，
  必须统一用 `--apply-schedule`，让程序按**执行时刻**自己算该用哪一档（幂等），否则白天/晚上会互相覆盖。
- **打包环境无 tkinter** → 打包后 exe 报 `ModuleNotFoundError: No module named 'tkinter'`。
  必须用含 tkinter 的完整版 Python 打包（本机：`WindowsApps\python.exe` 3.14 有；
  托管版 3.13 没有）。
- **`rm` 被 safe-delete（回收站）拦截** → `rm -rf dist && pyinstaller` 的 `&&` 链会直接中断、打包不执行。
  改用**全新输出目录**（`--distpath dist_v110b`），不要"先删后建"。
- **`--windowed` 打包后 `sys.stdout/stderr` 可能是 None** → `print()` / `sys.stderr.write()`
  会在没控制台时抛异常。CLI 输出统一走 `_out()` / `_err()` 包装。
- **非法参数绝不能"猜"** → `clamp_level("abc")` 会静默变成 `0`，直接跑就是**把屏幕调黑**；
  序号写错若退化成"全部"则会动到不该动的屏。CLI 用 `parse_level_arg()` / `parse_index_arg()`
  严格校验，不合法一律退出码 `2`。
- **DDC/CI 前提** → 显示器 OSD 必须开启 DDC/CI，且 HDMI/DP/DVI 直连；否则枚举不到显示器。
- **计划任务里的程序路径** → 打包态用自身 exe；**源码态必须把脚本路径带上**
  （`sys.executable` 是 `python.exe` 而非目标程序）。见 `task_target()`。
- **`monitorcontrol` 的 Monitor 没有 `model` 属性** → 型号要从 `get_vcp_capabilities()['model']` 取，
  且**实测约 3 秒/台**，必须放后台线程，不能和亮度读取串行。
- **tkinter 不是线程安全的** → 后台线程里调 `root.after()` 会抛
  `RuntimeError: main thread is not in main loop`。统一走「后台线程 → `queue` → 主线程轮询」。
- **深色勾选框别自己造** → `tk.Checkbutton` 的 `selectcolor` 会让"未勾选也带色"；
  clam 主题的 ttk 勾选框选中态图形**不吃 `indicatorcolor`**（实测无白色像素）。
  正解是用 `install_dark_tickbox()`：ttk 官方 `element_create` + `layout` 换掉
  `Checkbutton.indicator`，图片用 `PhotoImage` 纯代码画，仍是原生 `ttk.Checkbutton`。
- **状态用变量驱动，别依赖控件的"选择集"** → 显示器列表用 `ttk.Checkbutton` + `BooleanVar`
  （+ `trace_add`），不用 `Listbox.curselection()`。后者在重建时会因
  **Tk 虚拟事件同步派发**而误触发一串回调，导致读取目标集错乱。
- **`style.layout` 里别给 `Checkbutton.label` 传 `padding`** → 报
  `TclError: Invalid -children value`；间距要用 `-indicatormargin`（左 上 右 下）。

## 约定

- UI 文案、代码注释、文档统一用**中文**
- 单文件交付：主脚本独立可运行，无外部资源依赖
- **公开仓库红线**：禁止提交 token / 密码 / API Key / 本地绝对路径 / 个人信息

## 常用命令

```bash
python monitor_brightness.py                    # 运行 GUI
python monitor_brightness.py --list             # 列出显示器
python monitor_brightness.py --selftest         # 打印档位判定自检表（不碰显示器）
python monitor_brightness.py --apply-schedule   # 按当前时间套用档位
python monitor_brightness.py --set 0,1 50       # 命令行设亮度（all 也可）
```

- 打包：见 [README](README.md)「从源码运行」
- 验证 exe（**无副作用**）：`MonitorBrightness.exe --set 0 abc` → 应退出码 `2` 并报参数错误；
  `--selftest` 应打印档位表且退出码 `0`。
  ⚠️ **不要**拿真参数试跑（`--set 0 50` 会真的改显示器的亮度）。

## 详细规则（按需 @引用）

- 当前内容未超阈值（< 150 词），暂未拆分出 `rules/`。
