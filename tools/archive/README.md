# 历史审计脚本（归档）

这三个脚本是 R2 拆分前针对单文件 `monitor_brightness.py` 写的结构分析工具：

- `global_audit.py`：大函数/结构全景
- `audit_layers.py`：候选模块分组与依赖方向
- `audit_edges.py`：可疑跨模块引用

2026-11-28 完成 P0/P1 清理后，结构约束已固化到
`tests/test_architecture.py`（分层 / 文件 700 行 / 函数 60 行 / legacy 隔离），
静态检查由 `ruff.toml` 的最小基线接管。这些脚本不再维护，仅留作历史参考；
它们仍假设主文件包含全部实现，直接运行会得到空或过时结果。
