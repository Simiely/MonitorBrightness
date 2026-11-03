# -*- coding: utf-8 -*-
"""打包脚本（体积优化版）。

要点：
1. 进程内清掉被污染的 TCL/TK 环境变量（shell 的 env -u 不可靠，且空字符串
   会让 PyInstaller 把 tkinter 判成 missing）。
2. 排除 Pillow 用不到的图像编解码插件与可选二进制：本程序只创建内存 RGBA
   位图 + ImageTk 显示 + 托盘 ICO，不打开任何图像文件。PIL.Image.init() 对
   插件缺失去是"静默跳过"（设计如此），所以排除是安全的。
3. 可选 UPX 压缩（--upx-dir），再缩 30% 左右；个别杀软可能误报，需要时再用。
"""
import os

VERSION_DIR = "dist_v186"          # ← 每次打包改这里
for k in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONPATH"):
    os.environ.pop(k, None)
assert "TCL_LIBRARY" not in os.environ

import PyInstaller.__main__

# 只创建内存 RGBA 位图 / ImageTk / 托盘 ICO：这些插件与可选二进制用不到
EXCLUDES = [
    # -- Pillow 图像编解码插件（Image.init() 对缺失插件静默跳过）
    "--exclude-module", "PIL.Jpeg2KImagePlugin",
    "--exclude-module", "PIL.WebPImagePlugin",
    "--exclude-module", "PIL.TiffImagePlugin",
    "--exclude-module", "PIL.JpegImagePlugin",
    "--exclude-module", "PIL.MpoImagePlugin",
    "--exclude-module", "PIL.PdfImagePlugin",
    "--exclude-module", "PIL.EpsImagePlugin",
    "--exclude-module", "PIL.DdsImagePlugin",
    "--exclude-module", "PIL.MicImagePlugin",
    "--exclude-module", "PIL.McIdasImagePlugin",
    "--exclude-module", "PIL.SpiderImagePlugin",
    "--exclude-module", "PIL.FliImagePlugin",
    "--exclude-module", "PIL.FpxImagePlugin",
    "--exclude-module", "PIL.GbrImagePlugin",
    "--exclude-module", "PIL.ImImagePlugin",
    "--exclude-module", "PIL.ImtImagePlugin",
    "--exclude-module", "PIL.IptcImagePlugin",
    "--exclude-module", "PIL.PcdImagePlugin",
    "--exclude-module", "PIL.PcxImagePlugin",
    "--exclude-module", "PIL.PixarImagePlugin",
    "--exclude-module", "PIL.SgiImagePlugin",
    "--exclude-module", "PIL.SunImagePlugin",
    "--exclude-module", "PIL.WmfImagePlugin",
    "--exclude-module", "PIL.XbmImagePlugin",
    "--exclude-module", "PIL.XpmImagePlugin",
    "--exclude-module", "PIL.BlpImagePlugin",
    "--exclude-module", "PIL.CurImagePlugin",
    "--exclude-module", "PIL.Hdf5StubImagePlugin",
    "--exclude-module", "PIL.MpegImagePlugin",
    # -- Pillow 可选二进制（freetype/lcms/webp/量化器，均未使用）
    "--exclude-module", "PIL._imagingft",
    "--exclude-module", "PIL._imagingcms",
    "--exclude-module", "PIL._webp",
    "--exclude-module", "PIL._imagingquant",
    # -- numpy（PIL._typing/Image 的可选依赖，运行时从不导入；OpenBLAS DLL 20MB+）
    "--exclude-module", "numpy",
    "--exclude-module", "numpy.typing",
    # -- AVIF 插件（自带 4.3MB 的 _avif.pyd，用不到）
    "--exclude-module", "PIL.AvifImagePlugin",
    "--exclude-module", "PIL._avif",
    # -- stdlib 里用不到的
    "--exclude-module", "unittest",
    "--exclude-module", "pydoc_data",
]

args = [
    "--noconfirm", "--onefile", "--windowed",
    "--name", "MonitorBrightness",
    "--distpath", VERSION_DIR,
    "--workpath", "build_tmp",
    "--specpath", ".",
] + EXCLUDES + ["monitor_brightness.py"]

PyInstaller.__main__.run(args)
print("BUILD DONE ->", VERSION_DIR)
