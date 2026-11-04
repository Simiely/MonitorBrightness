# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-11-04（v1.9.7：快速点亮 0x60 翻转、8 秒二击升级、wake_fast_flip 开关）
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

## 最高优先级硬约束（v1.9.3 起，高于其它一切规则）

**只修被复现的 bug、只做用户明确要求的改动。**

- 任何**默认值、操作流程、交互顺序**的变更，必须先向用户说明并得到同意，才能写代码。
- 不允许把「理论上的风险」当成 bug 顺手修掉。没有用户可复现现象的问题，只能记录，不能改。
- 改动前先问两句：「这是被用户报告过的 bug 吗？」「这是用户点名要的吗？」两者都不是 → 不动。
- 用户已验收的路径（尤其是熄灭/唤醒）是**基线**，不是"待优化代码"。要改先说服用户，不许先改后解释。
- **反例（v1.9.0）**：未经要求把 `screen_off_method` 默认从 `ddc` 改成 `system`、把点亮
  `mouse_event` 改成 `SetThreadExecutionState`、把句柄生命周期改成"谁用谁销毁"、
  加"启动补执行"——都不是用户报告的 bug，也不是用户点名的改动，直接改变了用户已经
  验证过的熄灭/唤醒行为。v1.9.3 已把这些全部回滚。**这就是"没有 bug 却改操作逻辑"的代价。**

## 技术栈

- **Python 3.10+**（打包用**完整版**，**必须含 tkinter**）
- GUI：**tkinter**（内置，主用）/ **PySide6**（可选变体，体积大）
- 显示器控制：**Windows `dxva2` 原生 DDC/CI**（VCP `0x10` 亮度 / `0x60` 输入源 /
  `0xD6` 电源模式）。v1.7.0 起**不再使用 `monitorcontrol`**（它在 Python 3.14 上枚举会崩，
  且与自己的枚举各排一次序）
- 打包：**PyInstaller** `--onefile --windowed`
- 定时：Windows **任务计划程序**，v1.1.0 起改用 **PowerShell `Register-ScheduledTask`**
  （**不再用 `schtasks.exe`**，原因见下方关键坑）

## 关键坑（越具体越好）

- **别给 tkinter 控件赋 `self._w`** → 那是控件路径（`Misc._w`），覆盖后再建子控件会
  `TypeError: unsupported operand type(s) for +: 'int' and 'str'`。存宽度用 `_width`。
- **Toplevel 弹层必须被持有引用** → 只 `SettingsSheet(self)` 不存变量，对象被 GC 时
  窗口连同内容一起消失，表现是"点了设置没反应"。用 `self._sheet = ...` 持有。
- **弹层尺寸要在内容搭完后设** → 基类构造时 body 是空的，`winfo_reqheight()` 只有 144，
  被 `resizable(False)` 锁死后内容被裁掉。子类最后调 `show()`。
- **控件必须在目标父容器内创建** → 先把 Switch 建在卡片上、再 `row.pack(...)`，
  它会跑到卡片别处（pack 只认自己的 master）。写成 `row(..., builder=lambda p: ...)`。
- **打包环境变量必须是"真 unset"** → `TCL_LIBRARY=`（空字符串）会让 PyInstaller 把
  tkinter 判成 missing，产出 `No module named 'tkinter'` 的废 exe；必须
  `env -u TCL_LIBRARY -u TK_LIBRARY`。若 shell 里 `env -u` 失效（别名的 py 会静默退出、
  stdout 丢失），就在 Python 进程内 `os.environ.pop(...)` 后调
  `PyInstaller.__main__.run(...)`（项目里留有 _build.py 模板）。
- **UI 流程测试的调度陷阱会复发** → 同一晚踩了两次（链式 after + 顶层 after 双调度，
  后跑的覆盖前一步结果，制造"按钮没反应"假象）。写测试先检查调度入口唯一。
- **Tk Canvas 圆角没有抗锯齿（Windows）** → CustomTkinter 官方文档确认只有 macOS
  有。要设计稿级的圆角只能 Pillow 4x 超采样 + LANCZOS 缩小成带 alpha 的
  PhotoImage 贴 Canvas（Tk 8.6+ 支持图片 alpha）；`smooth=True` 多边形救不了。
  PhotoImage 必须全局持引用，渲染按参数缓存。
- **异步流程测试只允许一个调度入口** → 测试脚本里"链式 after(1000, next) +
  顶层 after(N, step)"会把每步跑两遍，结果互相覆盖，制造出"滑块下发错值/预设被吞"
  的假阴性（排查半天发现代码完全正确）。写 UI 流程测试时只用一种调度方式。
- **截图核验用 PrintWindow** → 屏幕抓取会被别的窗口挡住，抓到别人的界面（踩过两次）；
  用 `PrintWindow(hwnd, hdc, 2)` 抓窗口自身内容才可靠。
- **PostMessage 点击 Tk 控件会被静默丢弃** → Tk 的 Windows 端口用线程键盘状态里的
  `GetKeyState(VK_LBUTTON)` 过滤合成鼠标消息。取证时必须在 `WM_LBUTTONDOWN` 前
  `SetKeyboardState` 置 `VK_LBUTTON=0x80`、`WM_LBUTTONUP` 前清掉（只影响本线程），
  中间各自 `update()` 泵一次事件；否则 PostMessage 返回成功但绑定不触发。
- **成功提示窗只在成功路径弹** → 失败一律 `showerror` + 状态栏；启动自动自愈不弹窗
  （状态卡/事件日志反馈），避免每次开机糊脸。后台回调先 `after(0, …)` 再碰 UI。
- **🔴 绝不能拿用户真机屏幕做"熄灭"类破坏性测试** → v1.7.0 我就这么干了一次：
  写 `0xD6=2` 验证唤醒逻辑，结果面板黑了、所有软件手段都救不回来，
  用户只能自己按物理电源键。**要验熄灭/点亮，只能在打桩层面验命令下发**
  （monkeypatch `screen_off_system` / `_write_vcp` 看调用序列），
  真机测试交给用户，或者先问一句。
- **`0xD6=2`（待机）的边界** → 本机实测：写完回读仍是 `1`、亮度照样可读，但背光真的灭了；
  唤醒走 v1.8.5 的多级 DDC（`0xD6=1` → 写回亮度 → 系统输入注入 → 按能力串切源）。
  v1.8.5 的默认熄灭方式就是 `ddc`，这是用户已验证的基线；`system` 是可选方式。
- **能力串里的数值是不带 0x 的十六进制** → 本机实测 `60(11 12 0F)` 是
  HDMI-1 / HDMI-2 / DP-1。按十进制解析会得到 `11/12/0` 这种不存在的口，
  拿它去切信号源 = 把画面切进黑洞。`_caps_inputs()` 用 `int(tok, 16)`。
- **「点亮」绝不能只有一招** → v1.6.x 唯一的唤醒手段是 `0x60` 切信号源，
  且"另一个口"写死成 `4`（DVI-2）。现代显示器没有 DVI-2，切过去即无信号、
  面板进省电，切回来能不能醒取决于链路重训练 → 换电脑后"有时点不亮"。
  v1.8.5 的唤醒顺序是：`0xD6=1` → 写回亮度 → 系统输入注入（1px `mouse_event`
  + `SC_MONITORPOWER 2→-1`）→ 按能力串选口切信号源（可关）。**这是已验证基线，别"优化"它。**
- **一台屏插两根线会被枚举成两个输出** → DP+HDMI 同时接，Windows 为每个输出建一个
  HMONITOR。此时切信号源可能切到没驱动的空口；且"熄 A、点 B"的错位会放大。
  所以：序号用设备名锚点解析；系统级熄灭天然没有这个问题。
- **`EnumDisplayMonitors` 顺序不保证稳定** → 只存下标会出现"熄的是这块、开机后点的是
  那块"。配置同时存 `monitor_ids`（`DISPLAY1/2`），执行前用 `resolve_monitors()` 重算。
- **`GetPhysicalMonitorsFromHMONITOR` 拿到的句柄必须 `DestroyPhysicalMonitor`** →
  旧版只取不还，反复操作会耗尽句柄，表现为"越用越不灵"。**注意：任何调用
  `enumerate_targets()` 的分支都必须有配对的 `release_targets()`**（v1.7.1 走查时
  就在 `resolve_monitors`/`selection_payload` 里抓到两处漏释放）。
- **ctypes 必须显式声明签名** → 64 位下 `HANDLE` 不声明会按 C int 截断；数组参数用
  `c_void_p` + `byref` 传，否则新版 Python 的严格检查会抛
  `expected LP_PHYSICAL_MONITOR instance`（`monitorcontrol` 4.2.0 就是这么挂的）。
- **写 VCP 前必须查能力串，未声明的值绝不盲写** → 微软 `SetVCPFeature` 文档原话：
  "Many monitors don't fully implement that standard; so your use of these commands
  might result in undefined monitor behavior."；ddcutil 文档也说明 I2C 层只确认
  "包收到了"，不确认"执行了"。本机面板 `D6(01 04 05)` 不含 02（待机），
  v1.7.0 盲写 02 → 背光灭且软件唤不回。现在 `screen_off_many()` 用
  `_caps_power()` 过滤，读不到就跳过。
- **"亮了但黑屏"按 GPU-面板握手失败处理** → 公开排查一致给出同一套恢复手段：
  切一次显示模式 / 让输出重新上电 / 重置驱动。所以唤醒序列里有一级
  `_output_recycle()`（`SC_MONITORPOWER 2 → -1`），只在屏幕本来就黑时执行。
- **`0xD6` 回读会撒谎（连待机也会）** → v1.7.0 现场取证：写 `0xD6=2` 待机后，
  回读仍是 `1`、`0x10` 亮度照样读得到。**绝不能拿回读当"点亮成功"的判据**，
  否则就是"程序说成功、屏幕黑着"（与 v1.5.0 事故同源）。界面只把回读当"证据"，
  真实亮灭交给「检测亮灭」+ 眼睛。
- **文本控件 `wraplength` 必须小于窗口宽度** → 依据行一开始设 560、窗口只有 504，
  文字顶到右边界被切掉（"看不出结果"的一半原因）。改 450 才完整换行。
- **"屏幕亮没亮"不能只看 DDC** → `0xD6` 会撒谎；系统级熄灭下真正可靠的判据是
  「熄灭时刻的 tick」vs「`GetLastInputInfo` 的 idle」：熄灭后出现过输入就说明已被唤醒。
  结论行只给人话，原始回读降级为依据行。
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
  **完全静默**，用户视角就是"点了没反应"（本项目真踩过两次：`self.catch_up_on_logon`
  拼错属性名；`add_row` 里 `hh, mm = parse_hhmm(...)` 解包 int 抛 `TypeError`）。
  v1.9.1 起根窗口装 `report_callback_exception`：异常落 `error.log` + 状态栏提示。
- **`parse_hhmm()` 返回的是"当天分钟数 int"，不是 `(时, 分)`** → 要递增请直接
  `m = (m + 60) % 1440`；弹窗/列表相关回归必须断言 **Toplevel `winfo_ismapped()` 且宽高 > 0**，
  只断言"列表 +1"会漏掉"弹窗根本没出现"。
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
- **重建任务不要先删后建** → 旧实现 `Unregister + Register` 每次都会把 `LastRunTime`
  重置成"从未运行"，用户看到的就是"定时从来没跑过"。v1.9.0 起优先 `Set-ScheduledTask`
  原地更新，失败才回退卸载重注册。
- **任务 action 会随 exe 目录过期** → 每版构建目录都在变（`dist_vXXX`），只靠"用户点应用"
  重建必然滞后。启动时必须读 `C:\Windows\System32\Tasks\<任务名>` 比对
  程序路径 / 参数 / 全部触发点 / `StartWhenAvailable`，不一致就静默重建。
- **启动自检只检查，不执行**（v1.9.4 用户裁定）→ 配置一致时状态行只显示
  「定时任务已就绪，下次运行 X」，不弹窗；**绝不能在启动时补执行档位、熄屏或改亮度**。
  v1.9.0 的 in-app 补执行 / 12 小时阈值 / `last_fired` 已全部删除，不要加回来。
- **配置必须原子写** → `config.json` 被外部读取（任务/诊断）时不能出现半个文件；
  用同目录 `mkstemp + os.replace`。读失败要留 `config.json.bad-*.json` 副本并提示，
  不能静默回默认值（用户设置还在坏文件里）。
- **能力串/亮度上限缓存必须带 EDID** → 输出口名（DISPLAY1/2）在换面板后不变，
  只按 dev 缓存会把旧面板的上限/待机能力套到新面板。key 用 `DEV#EDID 短哈希`。
- **快速点亮只是"先手"，不能替代完整阶梯**（v1.9.7）→ `0x60` 翻转仅在
  「`wake_fast_flip` 开 + 熄灭方式 ddc + 允许切源 + 记账为熄屏」时启用；同一台 8 秒内
  第二次点亮、或翻转失败，该台必须回退 `_wake_one` 完整阶梯并写明原因。
  `_wake_one` / `screen_off_cfg` / `screen_on_cfg` 是 v1.8.5 验收基线，逐字节不动；
  路由只加在统一入口 `screen_wake_entry`，别往 `_wake_one` 里塞分支。
- **`0x60` 翻转必须保证值真的变了** → 当前源已是 17 时不能写 17（那不构成边沿）；
  要从能力串里另选一个不同的声明值，写回后再回读校验，否则按失败回退。
- **点亮必须保留 `mouse_event` 1px** → v1.8.5 已验证的唤醒步骤，`ES_DISPLAY_REQUIRED`
  已撤销（它只重置显示空闲计时器，不产生输入事件）。别再"优化"。
- **唤醒线程句柄按 v1.8.5 管理** → 线程 `join(30)` 后统一 `release_targets(targets)`，
  不要改成"线程自己销毁 + 全局 deadline"。
- **默认熄灭方式就是 `ddc`** → 这是 v1.8.5 基线，未经用户同意不得改默认值；
  `system` 是可选方式。
- **时间控件不要再用左右拖动** → 用户明确反馈不好用；现在是可键入 `HH:MM`
  （兼容 `9:5` / 全角冒号 / `0905`）+ ▲▼ 15 分钟步进 + 常用预设。
- **Pillow 必须守卫 import** → 它只服务 GUI 的圆角贴图；缺 Pillow 时 CLI 必须照常可用，
  GUI 明确报错退出。README 的依赖表要与代码一致。
- **不要声明 DPI 感知**（v1.9.4 已撤销）→ 用户确认现有显示可接受；
  `ScrollArea` 限高沿用 v1.8.5 的 `winfo_screenheight() - 180`，不要用 `rcWork`。
- **计划任务里的程序路径** → 打包态用自身 exe；**源码态必须把脚本路径带上**
  （`sys.executable` 是 `python.exe` 而非目标程序）。见 `task_target()`。
- **型号只能从 MCCS 能力串里取** → `dxva2.GetCapabilitiesStringLength` +
  `CapabilitiesRequestAndCapabilitiesReply`，正则取 `model(...)`；
  **实测约 3 秒/台**，必须放后台线程，不能和亮度读取串行。
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
python monitor_brightness.py --list             # 列出显示器（含当前输入源）
python monitor_brightness.py --probe            # 实测每台显示器的亮灭（只读）
python monitor_brightness.py --diag             # 只读诊断报告（排查点不亮）
python monitor_brightness.py --selftest         # 打印档位判定自检表（不碰显示器）
python monitor_brightness.py --apply-schedule   # 按当前时间套用档位
python monitor_brightness.py --ensure-schedule  # 按当前配置/当前 exe 重建计划任务
python monitor_brightness.py --set 0,1 50       # 命令行设亮度（all 也可）
python -m unittest discover -s tests -v         # 不碰硬件的回归测试
```

- 打包：见 [README](README.md)「从源码运行」
- 验证 exe（**无副作用**）：`MonitorBrightness.exe --set 0 abc` → 应退出码 `2` 并报参数错误；
  `--selftest` 应打印档位表且退出码 `0`。
  ⚠️ **不要**拿真参数试跑（`--set 0 50` 会真的改显示器的亮度）。

## 详细规则（按需 @引用）

- 当前内容未超阈值（< 150 词），暂未拆分出 `rules/`。
