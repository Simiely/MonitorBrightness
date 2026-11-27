# 问题：显示器名称显示成 `<Monitor object at 0x...>`

**TL;DR**：`monitorcontrol` 的 `Monitor` **没有** `model` 属性，旧写法从未生效。

- **根因**：`getattr(m, "model", None)` 一直落到 `or str(m)`，于是打印对象 repr。
- **解决**：型号从 **MCCS 能力串** `get_vcp_capabilities()["model"]` 取；
  分辨率 / 主副屏 / 设备名（`\\.\DISPLAY1`）用 Win32 `EnumDisplayMonitors` 取。
  两者下标可对齐 —— 当时核对了 `monitorcontrol` 源码（它内部也是 `EnumDisplayMonitors`
  + 逐个 `GetPhysicalMonitorsFromHMONITOR`，顺序一致）与实测枚举结果。
  v1.7.0 起干脆不再依赖 `monitorcontrol`，型号直接由 `_caps_model()` 解析能力串。
- **预防**：双屏同型号时，靠「主/副屏 + 设备名」区分，已在界面标签里带上。
