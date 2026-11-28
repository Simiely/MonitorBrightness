# MonitorBrightness · 显示器亮度定时调节

[![Release](https://img.shields.io/github/v/release/Simiely/MonitorBrightness)](https://github.com/Simiely/MonitorBrightness/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4)
![exe](https://img.shields.io/badge/download-单文件%20exe-8A2BE2)

> Windows 外接显示器亮度调节小工具：走 **DDC/CI 硬件协议**真正调背光（非软件滤镜），
> 既能手动即时调节，也能按时间点自动调节——定时写入 Windows 任务计划程序，**无需常驻后台**。

## 特性

- ⏱️ **定时熄屏 / 点亮**：每个时间段可选「到点同时熄屏 / 点亮」—— 如 21:00 自动熄屏防窥
- 🫥 **系统托盘 + 开机自启**：可最小化到托盘常驻（悬停看定时摘要、菜单直接熄/亮屏、切换定时）
- 🖥️ **一键熄屏 / 点亮**：`--screen off` 或界面按钮；可设「N 分钟自动点亮」保险丝
- ⚡ **三个快捷预设**：`0% / 75% / 100%` 一键把**全部**显示器切到该亮度（值可改）
- 🔎 **亮灭状态可读**：点「检测」实测每台屏现在亮还是灭 + 当前亮度
- 🩺 **`--diag` 一键诊断**：枚举结果、每台的电源 / 输入源 / 亮度回读、能力串支持的口
- 🪟 **深色界面（Apple 风格）**：状态卡 + 分组卡片 + 自绘开关 / 分段 / 滑块
- 🖥️ **多显示器勾选**：每台前面一个勾选框，勾上哪几台就一起调；带「刷新」重新枚举
- 🎚️ **手动调节**：拖亮度滑块**松手即写回**（拖动过程不刷 DDC）
- ⏰ **定时调节（多时间段）**：数量不限，点「+ 添加时间段」随时增加；时间可**直接键入**
- ♻️ **自愈 + 启动自检**：每次启动校验任务是否仍指向当前 exe；**只检查配置，不会主动熄屏**
- 🧰 **唤醒分级**：默认先走「`0x60` 切源翻转」（实测 ~180ms），不成再升级完整阶梯
- 🔌 **硬件级调光**：DDC/CI（VCP `0x10`），不偏色、不降对比
- 📦 **单文件 exe**：免安装，双击即用（tkinter 打包，约 15 MB）
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

到 [Releases](https://github.com/Simiely/MonitorBrightness/releases) 下载 `MonitorBrightness_v*.zip`
（内含单文件 exe）→ 解压双击运行。

1. **前提**：先在显示器物理菜单（OSD）里把 **DDC/CI** 设为「开」；
2. 打开程序 → **勾选**要调的显示器（一个都不勾 = 全部）→ 拖滑块**松手即生效**（或点预设）；
3. 想定时：打开「启用每日定时」→ 点「+ 添加时间段」增加任意多档（每档 = 时间 + 亮度 +
   到点是否熄屏/点亮）。开关切换、增删改都会自动写入任务计划，状态行实时显示下次运行 / 上次结果。
   想按当前配置强制重建时点状态行右侧的「应用」。

> 首次运行若被 SmartScreen 拦截，点「更多信息 → 仍要运行」即可（本机自打包程序）。

### 方式 B：从源码运行

需**完整版 Python 3.10+（含 tkinter）**。亮度 / 枚举直接走 Windows `dxva2` 的 DDC/CI
接口（v1.7.0 起不再需要 `monitorcontrol`）。

| 用途 | 需要 |
|---|---|
| 图形界面 | `pillow`（自绘圆角抗锯齿贴图；exe 已内置） |
| 系统托盘 | `pystray`（可选；没有就自动不启用托盘） |
| 纯命令行 | 无第三方依赖，**缺 Pillow 也能跑** |

```bash
pip install pillow pystray
python monitor_brightness.py
```

打包成单文件 exe（打包环境必须是**含 tkinter 的完整版 Python**）：

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name MonitorBrightness monitor_brightness.py
```

> `legacy/monitor_brightness_qt.py` 是 v1.0.0 的 PySide6/Qt 旧变体（单选 / 浅色 /
> 旧定时逻辑），**不随新版分发**；它只读写 `config_qt_legacy.json`，不会覆盖新版
> `config.json`。仅作历史参考，不建议继续使用。

## 常见问题（高频 4 条）

**调不动 / 提示「未检测到 DDC/CI 显示器」？** ① OSD 里 DDC/CI 是否已开；② 是否 HDMI / DP / DVI
直连（不走 KVM、USB 转接）；③ 笔记本内置屏不支持。

**定时到点没反应？** 先看「定时」分组底部的任务状态行：每次启动都会校验任务路径 / 参数 /
触发点，不一致会在后台静默重建。若显示「补跑未开」，点状态行右侧的「应用」强制重建，
或直接跑 `--ensure-schedule`；「上次运行」一直是"从未运行"就跑 `--diag` 看结果码。

**点亮没反应，或者点完反而更黑？** 默认「熄灭方式 = 单屏待机」（DDC 让面板自己睡，键鼠不会
点亮，适合远程防窥）。唤不回来时按显示器电源键即可恢复；更在意"一定亮得回来"可在
「设置 → 熄灭方式」切回**系统级**（`SC_MONITORPOWER 2`，代价是整组一起灭、键鼠会点亮）。

**界面点了没反应？** v1.9.1 起所有 Tk 回调异常都会写 `%APPDATA%\MonitorBrightness\error.log`，
并在状态栏显示「界面异常：…」。把该日志发回即可定位。

> 完整 FAQ（多屏识别、DDC 待机风险、远程操作选型、唤醒阶梯、`0x60` 快速点亮）
> 见 **[docs/使用指南.md](docs/使用指南.md)**。

## 测试

不碰硬件的回归测试（配置原子写、坏配置备份、时间解析、启动自检只检查不执行、
任务参数拼装、「添加时间段」点击回归）：

```bash
python -m unittest discover -s tests -v
```

## 项目文档

| 文件 | 内容 |
|---|---|
| [`docs/index.md`](docs/index.md) | **文档总索引**（先看这个） |
| [`AGENTS.md`](AGENTS.md) | 给 AI / 未来自己的项目规则、关键坑、常用命令 |
| [`DEVELOPMENT.md`](DEVELOPMENT.md) | 架构总览 + 13 篇问题记录索引 |
| [`CHANGELOG.md`](CHANGELOG.md) | 版本变更记录 |
| [`rules/坑位.md`](rules/坑位.md) | 全部关键坑（60 条，`AGENTS.md` 只留最该先看的 5 条） |

文档组织遵循 [knowledge-base 单项目规范](https://github.com/Simiely/knowledge-base)。
