# CHANGELOG

本文件记录项目所有值得注意的变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] - 2026-09-25

### 新增

- 图形界面（tkinter）：显示器选择、亮度滑块即时调节
- 每日定时：白天 / 晚上两个时间点与亮度，一键写入 Windows 任务计划程序
- 命令行模式：`--set <index> <value>`，供任务计划静默调用
- 单文件 exe 打包（PyInstaller `--onefile --windowed`，约 14 MB）
- 配置持久化：`%APPDATA%/MonitorBrightness/config.json`
- 附 PySide6/Qt 界面变体源码（`monitor_brightness_qt.py`）

[1.0.0]: https://github.com/Simiely/MonitorBrightness/releases/tag/v1.0.0
