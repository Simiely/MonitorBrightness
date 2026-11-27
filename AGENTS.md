# AGENTS.md · 项目规则

> 📌 **文档基线**：2026-11-27（commit `2902c53`）E3/E4 + 接回亮度修复 + GUI 显示断开屏（默认 ddc 不变，60 tests OK）　← 前序：2026-11-27（commit `7ea0b07`）按单项目规范 2.1.5 整理：README / DEVELOPMENT 拆分、13 篇问题记录、repo-kit 2.9.1、CI 接入回归测试　← 更早：2026-11-17（commit `9777404`）接入机器层并压缩 AGENTS
> **更新文档/代码后，请更新此行**（日期 + 新 commit hash），并在 CHANGELOG 追加版本

## 最高优先级硬约束（v1.9.3 起，高于其它一切规则）

**只修被复现的 bug、只做用户明确要求的改动。**

- 任何**默认值、操作流程、交互顺序**的变更，必须先向用户说明并得到同意，才能写代码。
- 不允许把「理论上的风险」当成 bug 顺手修掉。没有用户可复现现象的问题，只能记录，不能改。
- 改动前先问两句：「这是被用户报告过的 bug 吗？」「这是用户点名要的吗？」两者都不是 → 不动。
- 用户已验收的路径（尤其是熄灭/唤醒）是**基线**，不是"待优化代码"。要改先说服用户，不许先改后解释。
- **反例（v1.9.0）**：未经要求把 `screen_off_method` 默认从 `ddc` 改成 `system`、把点亮
  `mouse_event` 改成 `SetThreadExecutionState`、把句柄生命周期改成"谁用谁销毁"、
  加"启动补执行"——都不是用户报告的 bug，也不是用户点名的改动，直接改变了用户已经
  验证过的熄灭/唤醒行为。v1.9.3 已把这些全部回滚。**这就是"没有 bug 却改操作逻辑"的代价。**

## 技术栈

- **Python 3.10+**（打包用**完整版**，**必须含 tkinter**）
- GUI：**tkinter**（内置，主用）/ **PySide6**（可选变体，体积大）
- 显示器控制：**Windows `dxva2` 原生 DDC/CI**（VCP `0x10` 亮度 / `0x60` 输入源 /
  `0xD6` 电源模式）。v1.7.0 起**不再使用 `monitorcontrol`**（它在 Python 3.14 上枚举会崩，
  且与自己的枚举各排一次序）
- 打包：**PyInstaller** `--onefile --windowed`
- 定时：Windows **任务计划程序**，v1.1.0 起改用 **PowerShell `Register-ScheduledTask`**
  （**不再用 `schtasks.exe`**，原因见下方关键坑）

## 关键坑（先看这 5 条，其余见 rules/）

- **别给 tkinter 控件赋 `self._w`** → 那是控件路径，覆盖后建子控件会 `TypeError`。存宽度用 `_width`。
- **Toplevel 弹层必须被持有引用** → 不存变量会在 GC 时连同内容消失，表现是「点了设置没反应」。
- **打包环境变量必须是「真 unset」** → `TCL_LIBRARY=`（空串）会让 PyInstaller 判 tkinter missing，产出废 exe。
- **🔴 绝不能用真机屏幕做「熄灭」类破坏性测试** → v1.7.0 干过一次，面板黑了只能按电源键。要验就 monkeypatch 命令下发。
- **能力串里的数值是不带 0x 的十六进制** → 按十进制解析会切到不存在的口（画面进黑洞），用 `int(tok, 16)`。

其余 55 条（UI 调度、圆角抗锯齿、截图核验、信号源切换、双线枚举等）见 [rules/坑位.md](rules/坑位.md)。

## 约定

- UI 文案、代码注释、文档统一用**中文**
- 单文件交付：主脚本独立可运行，无外部资源依赖
- **公开仓库红线**：禁止提交 token / 密码 / API Key / 本地绝对路径 / 个人信息

## 常用命令

```bash
python monitor_brightness.py                    # 运行 GUI
python monitor_brightness.py --list             # 列出显示器（含当前输入源）
python monitor_brightness.py --probe            # 实测每台显示器的亮灭（只读）
python monitor_brightness.py --diag             # 只读诊断报告（排查点不亮）
python monitor_brightness.py --selftest         # 打印档位判定自检表（不碰显示器）
python monitor_brightness.py --apply-schedule   # 按当前时间套用档位
python monitor_brightness.py --ensure-schedule  # 按当前配置/当前 exe 重建计划任务
python monitor_brightness.py --set 0,1 50       # 命令行设亮度（all 也可）
python -m unittest discover -s tests -v         # 不碰硬件的回归测试
```

- 打包：见 [README](README.md)「从源码运行」
- 验证 exe（**无副作用**）：`MonitorBrightness.exe --set 0 abc` → 应退出码 `2` 并报参数错误；
  `--selftest` 应打印档位表且退出码 `0`。
  ⚠️ **不要**拿真参数试跑（`--set 0 50` 会真的改显示器的亮度）。

## 边界

本仓库采用 repo-kit 的 `link-only` profile：机器层只查**本地链接、锚点与入口**，
不要求 frontmatter（`schema/taxonomy.json` 已把 `**/*.md` 全部豁免）。
内容真源由本仓库自己的文档管线保证，**语义正确性靠人审**——
`REPO CHECK: PASS` 只能证明"结构整齐、链接可达"，不能证明文档说的是真的。

## 详细规则（按需 @引用）

- [rules/坑位.md](rules/坑位.md)：全部关键坑（60 条），`AGENTS.md` 只留最该先看的 5 条。
