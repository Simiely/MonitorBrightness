# MonitorBrightness · 显示器亮度定时调节

> Windows 外接显示器亮度调节小工具：走 **DDC/CI 硬件协议**真正调背光（非软件滤镜），
> 既能手动即时调节，也能按时间点自动调节——定时写入 Windows 任务计划程序，**无需常驻后台**。

## 特性

- 🎚️ **手动调节**：图形界面选显示器 → 拖亮度滑块 → 立即生效
- ⏰ **定时调节**：设置白天 / 晚上两个时间点与亮度，一键写入任务计划，到点自动执行
- 🖥️ **硬件级调光**：DDC/CI（VCP 特性码 `0x10` 亮度），不偏色、不降对比
- 📦 **单文件 exe**：免安装，双击即用（tkinter 打包，约 14 MB）
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
2. 打开程序 → 选显示器 → 拖亮度滑块 → 点「应用到显示器」，屏幕立刻变化；
3. 想定时：勾「启用每日定时」→ 填白天 / 晚上时间与亮度（默认 `09:00 → 80%`、`20:00 → 30%`）→ 点「保存并启用定时」。

> 首次运行若被 Windows SmartScreen 拦截，点「更多信息 → 仍要运行」即可（本机自打包程序）。

### 方式 B：从源码运行

需**完整版 Python 3.10+（含 tkinter）**：

```bash
pip install monitorcontrol
python monitor_brightness.py
```

打包成单文件 exe：

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name MonitorBrightness \
  --hidden-import=monitorcontrol --hidden-import=win32api --hidden-import=win32gui \
  monitor_brightness.py
```

> 仓库另附 `monitor_brightness_qt.py`（PySide6/Qt 界面变体，观感更现代但体积约 48 MB），功能等价，按需选用。

## 命令行模式（供任务计划调用）

```bat
MonitorBrightness.exe --set <显示器序号> <亮度 0-100>
:: 例：把 0 号显示器亮度设为 50
MonitorBrightness.exe --set 0 50
```

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

**多显示器？**
界面下拉里选择目标显示器即可；程序按序号记忆。系统重排序号后可能需重设一次。

## 备选方案（纯命令行）

若不想装本程序，可用 NirSoft **ControlMyMonitor** 走同样的 DDC/CI：

```bat
ControlMyMonitor.exe /SetValue Primary 10 80
```

配合 Windows「任务计划程序」定时调用即可。
