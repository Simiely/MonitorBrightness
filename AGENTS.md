# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-09-25（commit `ed43e41`）完成四件套初始化
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

## 技术栈

- **Python 3.10+**（打包用**完整版**，**必须含 tkinter**）
- GUI：**tkinter**（内置，主用）/ **PySide6**（可选变体，体积大）
- 显示器控制：**`monitorcontrol`**（Windows 走 DDC/CI，VCP 特性码 `0x10` 亮度）
- 打包：**PyInstaller** `--onefile --windowed`
- 定时：Windows **`schtasks`**（任务计划程序）

## 关键坑（越具体越好）

- **打包环境无 tkinter** → 打包后 exe 报 `ModuleNotFoundError: No module named 'tkinter'`。必须用含 tkinter 的完整版 Python 打包，或改用 PySide6。
- **`rm` 被 safe-delete（回收站）拦截** → `rm -rf dist && pyinstaller` 的 `&&` 链会直接中断、打包不执行。改用**全新输出目录**（`--distpath dist_new`）。
- **DDC/CI 前提** → 显示器 OSD 必须开启 DDC/CI，且 HDMI/DP/DVI 直连；否则枚举不到显示器。
- **任务计划路径** → `schtasks /TR` 必须用**带引号的完整路径**；打包后以 `sys.executable` 取得 exe 路径。
- **`--windowed` 打包** → GUI 不弹控制台；CLI 模式（`--set`）仍可静默执行。

## 约定

- UI 文案、代码注释、文档统一用**中文**
- 单文件交付：主脚本独立可运行，无外部资源依赖
- **公开仓库红线**：禁止提交 token / 密码 / API Key / 本地绝对路径 / 个人信息

## 常用命令

```bash
python monitor_brightness.py            # 运行 GUI
python monitor_brightness.py --set 0 50 # 命令行设亮度
```

- 打包：见 [README](README.md)「从源码运行」
- 验证 exe：`MonitorBrightness.exe --set 0 50`（退出码 0 即通过）

## 详细规则（按需 @引用）

- 当前内容未超阈值（< 150 词），暂未拆分出 `rules/`。
