# DEVELOPMENT.md · 开发文档

## 项目概览

MonitorBrightness 是一个 Windows 桌面小工具，用于**外接显示器亮度的手动 / 定时调节**。

- 通过 **DDC/CI** 协议直接向显示器下发亮度指令（VCP 特性码 `0x10`），属**硬件级背光调节**，非软件滤镜，不偏色。
- 定时功能**不依赖常驻进程**：程序把「时间点 + 亮度」写入 **Windows 任务计划程序**，由系统到点调用 exe 的命令行模式执行。
- 打包为单文件 exe，绿色免安装。

## 架构说明

```
┌──────────────────────────────┐
│  GUI (tkinter)               │  显示器下拉 / 亮度滑块 / 定时设置
│  class App                   │
└───────────┬──────────────────┘
            │ 调用
┌───────────▼──────────────────┐
│  亮度控制层                   │  list_monitors / set_brightness / get_brightness
│  monitorcontrol (DDC/CI)     │  → Windows: dxva2.dll（GetPhysicalMonitors… / SetVCPFeature）
└───────────┬──────────────────┘
            │
┌───────────▼──────────────────┐
│  定时层                       │  register_tasks / unregister_tasks
│  schtasks                    │  → Windows 任务计划程序（MonitorBrightness_Day / _Night）
└──────────────────────────────┘

CLI 入口:    monitor_brightness.py --set <index> <value>   （供任务计划调用，无 GUI）
配置持久化:  %APPDATA%/MonitorBrightness/config.json
```

**单文件内的模块分区**（按注释分节）：

1. 配置读写 —— `load_config` / `save_config`
2. 亮度控制 —— `list_monitors` / `set_brightness` / `get_brightness`
3. 任务计划 —— `register_tasks` / `unregister_tasks`
4. 命令行入口 —— `cli_set`
5. 图形界面 —— `class App`（tkinter）

## 关键问题与方案

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

### 问题：定时任务到点不生效

**TL;DR**：任务计划里的程序路径未正确引用，或注册后移动了 exe。

- **问题**：到了设定时间亮度没有变化。
- **根因**：`schtasks /TR` 的程序路径未加引号、含空格时被错误解析；或注册后 exe 被移动导致路径失效。
- **解决**：`/TR` 使用**带引号的完整路径**（打包后用 `sys.executable`）；exe 固定放置不再移动。
- **预防**：注册任务后到「任务计划程序」确认 `MonitorBrightness_Day` / `_Night` 存在，并可手动「运行」验证。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。
