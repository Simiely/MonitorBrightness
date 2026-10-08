# MonitorBrightness · 显示器亮度定时调节

> Windows 外接显示器亮度调节小工具：走 **DDC/CI 硬件协议**真正调背光（非软件滤镜），
> 既能手动即时调节，也能按时间点自动调节——定时写入 Windows 任务计划程序，**无需常驻后台**。

## 特性

- 🌙 **深色界面**：整体深色主题（含 Windows 标题栏），不刺眼
- 🖥️ **多显示器勾选**：**每台显示器前面一个勾选框**，勾上哪几台就一起调；带「刷新」重新枚举
- 🎚️ **手动调节**：拖亮度滑块 → 点「应用到选中显示器」→ 立即生效
- ⏰ **定时调节（多时间段）**：时间段数量不限 —— 点「+ 添加时间段」随时增加，每行 时间 + 亮度，可删除；到点自动切到该档亮度
- 🔁 **错过会补跑**：电脑关机 / 睡眠错过时间点，开机后会补上（见下方「定时可靠性」）
- 🔌 **硬件级调光**：DDC/CI（VCP 特性码 `0x10` 亮度），不偏色、不降对比
- 📦 **单文件 exe**：免安装，双击即用（tkinter 打包，约 13 MB）
- 🧩 **零常驻**：定时由系统调度，程序关掉也照常执行

## 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 |
| 显示器 | **支持 DDC/CI**，且已在显示器 OSD 菜单中**开启 DDC/CI** |
| 连接 | HDMI / DisplayPort / DVI **直连**（避免 KVM、USB 转接、部分扩展坞） |

> ⚠️ 笔记本**内置屏**不走 DDC/CI，本工具针对**外接显示器**。

## 快速开始

### 方式 A：下载 exe（推荐）

到 [Releases](../../releases) 下载 `MonitorBrightness_lite.exe` → 双击运行。

1. **前提**：先在显示器物理菜单（OSD）里把 **DDC/CI** 设为「开」；
2. 打开程序 → 在列表里**勾选**要调的显示器（每台一个勾选框；**一个都不勾 = 全部**）→ 拖亮度滑块 → 点「应用到选中显示器」，屏幕立刻变化；
3. 想定时：勾「启用每日定时」→ 点「+ 添加时间段」增加任意多档（每行填 时间 + 亮度，如 `08:30 → 80%`、`13:00 → 60%`、`21:00 → 0%`）→ 点「保存并启用定时」。

> 首次运行若被 Windows SmartScreen 拦截，点「更多信息 → 仍要运行」即可（本机自打包程序）。

### 方式 B：从源码运行

需**完整版 Python 3.10+（含 tkinter）**：

```bash
pip install monitorcontrol        # 仅需这一条；GUI 用内置 tkinter，无第三方 GUI 依赖
python monitor_brightness.py
```

打包成单文件 exe：

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name MonitorBrightness \
  --collect-submodules monitorcontrol \
  monitor_brightness.py
```

> 打包环境必须是**含 tkinter 的完整版 Python**，否则打出来的 exe 一运行就报
> `No module named 'tkinter'`。
> 仓库另附 `monitor_brightness_qt.py`（PySide6/Qt 界面变体，观感更现代但体积约 48 MB），
> 它停留在 v1.0.0 的功能水平（单选 / 浅色 / 旧定时逻辑），按需自行改造。

## 定时可靠性（v1.1.0 修复）

v1.0.0 有个明确的缺陷：**电脑在设定时间点处于关机 / 睡眠状态时，到点那次调节会永远丢掉**。
原因是它用 `schtasks.exe` 注册任务，而 **`schtasks.exe` 命令行根本没有「错过就尽快补跑」这个开关**。

v1.1.0 的修法：

1. 改用 PowerShell `Register-ScheduledTask` + `New-ScheduledTaskSettingsSet -StartWhenAvailable`；
2. 任务动作统一改成 `--apply-schedule`，由**程序自己按执行时刻**判断该用白天还是晚上档
   （带 ±2 分钟抖动容错）。这样即便白天、晚上两条任务被同时补跑，结果也一致，不会互相覆盖；
3. 额外注册一条「登录后自动校准」任务：登录后 30 秒执行一次 `--apply-schedule`，
   保证**开机时间已经晚于切换点**时也能立刻补上。

程序界面底部会实时显示三条任务的状态、下次运行时间，以及 `补跑已开 / 补跑未开`，
可以直接在界面上确认修复是否生效。

## 命令行模式（供任务计划调用）

```bat
:: 立即设置（序号可写 all 或逗号分隔的多个）
MonitorBrightness.exe --set 0 50
MonitorBrightness.exe --set 0,1 50
MonitorBrightness.exe --set all 50

:: 按当前时间判定白天/晚上档位后下发（计划任务用的就是这条）
MonitorBrightness.exe --apply-schedule

:: 列出显示器 / 打印档位判定自检表
MonitorBrightness.exe --list
MonitorBrightness.exe --selftest
```

退出码：`0` 成功 · `1` 下发失败 · `2` **参数不合法**（亮度必须是 0-100 整数，序号必须是
`all` 或 `0,1` 这类写法——写错会直接报错退出，不会"猜"成 0 或"全部"）。

## 项目文档

| 文件 | 内容 |
|---|---|
| [`README.md`](README.md) | 本文件：项目简介、安装、快速开始 |
| [`AGENTS.md`](AGENTS.md) | 给 AI / 未来自己的项目规则、关键坑、常用命令 |
| [`DEVELOPMENT.md`](DEVELOPMENT.md) | 架构说明 + 关键问题记录（一坑一篇） |
| [`CHANGELOG.md`](CHANGELOG.md) | 版本变更记录 |

文档组织遵循 [knowledge-base 单项目规范](https://github.com/Simiely/knowledge-base)。

## 常见问题

**调不动 / 提示「未检测到 DDC/CI 显示器」？**
1. 显示器 OSD 菜单里 DDC/CI 是否已开启；
2. 是否用 HDMI / DP / DVI 直连（不要走 KVM / USB 转接）；
3. 笔记本内置屏不支持 DDC/CI。

**多显示器认不出是哪台？**
列表里每行都带**型号 · 分辨率 · 主/副屏 · 设备名**（如
`Legion R27qe Gen2 · 2560×1440 · 主显示器 · DISPLAY1`），同型号双屏也能区分。

**「当前亮度」一直是 `--`？**
型号名（MCCS 能力串）实测约 3 秒/台，界面会在后台异步补；若长时间为空说明该显示器
不返回 DDC/CI 数据（可点「读取当前亮度」重试）。

**定时到点没反应？**
先看界面底部的任务计划状态：若显示「补跑未开」，说明任务是用旧版本注册的，
重新点一次「保存并启用定时」即可按新规则覆盖。

## 备选方案（纯命令行）

若不想装本程序，可用 NirSoft **ControlMyMonitor** 走同样的 DDC/CI：

```bat
ControlMyMonitor.exe /SetValue Primary 10 80
```

配合 Windows「任务计划程序」定时调用即可（记得在任务属性里勾上
「如果错过计划开始时间，请尽快启动任务」，这是本工具 v1.1.0 自动帮你做的事）。
