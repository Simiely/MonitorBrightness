# 文档索引

本仓库的文档分四层：**门面**（README）→ **规则**（AGENTS + rules/）→ **开发**（DEVELOPMENT）→
**细节**（docs/）。

## 四件套

- [README](../README.md) — 项目简介、安装、快速开始、高频 FAQ
- [AGENTS](../AGENTS.md) — AI / 维护者入口：硬约束、技术栈、最该先看的 5 条坑
- [DEVELOPMENT](../DEVELOPMENT.md) — 架构总览 + 问题索引
- [CHANGELOG](../CHANGELOG.md) — 版本变更记录

## 规则

- [rules/坑位.md](../rules/坑位.md) — 全部 60 条关键坑（AGENTS 只留 5 条）

## 深入阅读

| 文档 | 内容 |
|---|---|
| [使用指南](使用指南.md) | 定时机制、完整 CLI、完整 FAQ（DDC 风险 / 远程 / 唤醒阶梯）、备选方案 |
| [架构](架构.md) | 数据流、分层与真实函数名、GUI 结构、持久化、健康度与已知债务 |
| [问题记录](问题记录/) | 13 篇一坑一篇（模板与 knowledge-base 完全一致，可直接提炼） |
| [architecture-review.md](architecture-review.md) | 架构复盘：模块边界与耦合点 |
| [rollback-classification.md](rollback-classification.md) | 回滚分类：哪些改动可回滚、哪些不可 |
| [rootcause-latency.md](rootcause-latency.md) | 延迟根因分析：从测量到定位 |

## 边界

本仓库采用 repo-kit 的 `link-only` profile：机器层只查链接、锚点与入口，不要求
frontmatter。语义正确性靠人审 —— `REPO CHECK: PASS` 只证明结构与链接，不证明内容为真。
