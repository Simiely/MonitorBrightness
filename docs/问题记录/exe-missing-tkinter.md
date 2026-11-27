# 问题：打包出的 GUI exe 报 `No module named 'tkinter'`

**TL;DR**：打包环境 Python 是精简版（无 tkinter），换完整版 Python 打包即可。

- **问题**：PyInstaller 打包后的 exe 一运行就崩溃，提示缺少 `tkinter`。
- **根因**：所用 Python 发行版为精简版，未包含 tkinter 标准库（tcl/tk），PyInstaller 自然也就打不进去。
- **解决**：改用**完整版 Python**（官方安装包，勾选 tcl/tk）重新打包；或改用不依赖 tkinter 的
  **PySide6/Qt** 写界面。
- **预防**：打包 GUI 前先自检 `python -c "import tkinter"`。
  另见 `_build.py` 的注释：`TCL_LIBRARY=`（空串）会让 PyInstaller 判 tkinter missing，
  必须在进程内 `os.environ.pop(...)` 做「真 unset」。
