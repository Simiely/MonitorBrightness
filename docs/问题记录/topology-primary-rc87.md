# 问题：不能用 CCD 直接断开主屏（rc=87）

**TL;DR**：`SetDisplayConfig` 想把主屏那条 path 清成 inactive，会返回
`ERROR_INVALID_PARAMETER (87)`，Windows 不允许。状态不会被改动，但必须当作失败处理。

- **现象**：2026-11-27 真机实验，对 `connectorInstance=1` 的主屏执行
  `脚本 --off <锚点> --apply` → `SetDisplayConfig 失败 rc=87`；
  回读确认两块屏仍都是 ACTIVE（没有半途状态）。
- **根因**：Windows 的显示拓扑要求至少保留一块活动显示输出作为主屏；
  CCD 没有"换主屏 + 断开"的原子操作，单独清掉主屏的 `PATH_ACTIVE` 是非法参数组合。
- **解决**：在动作前用 `EnumDisplayMonitors` 找出主屏的 GDI 名，
  与目标 path 的 active source 名比对，命中就直接拒绝并给出明确提示。
  真要支持主屏，必须做成两步事务（先 `SetDisplayConfig` 改主屏归属 → 再断开），
  本次不做。
- **预防**：护栏要在 `SetDisplayConfig` **之前**生效；任何 rc≠0 都必须原样上报，
  不能因为"回读看起来没变"就当成功。
- **证据**：`scripts/topology_switch.py` 的 `--off 68C7/1 --apply`（已加护栏，现在会被提前拒绝）；
  原始实验记录见 `docs/拓扑探针.md` C2/C3/C4 段。
