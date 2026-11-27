# 问题：定时任务在「开机时间已过」后不生效（v1.1.0 核心修复）

**TL;DR**：`schtasks.exe` 命令行没有「错过就尽快补跑」开关，必须改用 PowerShell
`Register-ScheduledTask` + `-StartWhenAvailable`；且任务要按执行时刻自行判档，才能幂等。

- **问题**：设 09:00 / 20:00 两档，若电脑在 09:00 处于关机或睡眠，醒来后亮度仍是前一档，
  到 20:00 之前都不会变。
- **根因（两层）**：
  1. Windows 任务计划**默认不补跑**错过的触发点，「Run task as soon as possible after a
     scheduled start is missed」默认是关闭的（`TaskSettings.StartWhenAvailable` 默认 `False`）。
  2. 而 **`schtasks.exe` 命令行没有对应参数** —— 只有 GUI、任务 XML、或 PowerShell 的
     `New-ScheduledTaskSettingsSet` 能设置它。v1.0.0 用 `schtasks /Create`，所以这个能力
     从一开始就拿不到。
- **解决**：
  1. 注册改走 `Register-ScheduledTask -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable ...)`。
  2. **同时**要把动作改成 `--apply-schedule`：因为开了补跑后，白天和晚上两条被错过的任务
     可能**都被补跑**，顺序不保证；若动作里写死档位，谁最后跑谁说了算，会得到错误的亮度。
     改成"程序按执行时刻自己判"之后，无论哪条跑、跑几条，结果都一致（幂等）。
  3. 再加一条**登录触发**任务（`-AtLogOn` + `Delay='PT30S'`）做兜底，保证开机时间已晚于
     切换点也能立刻补上，不必等 StartWhenAvailable 的 10 分钟排队延迟。
- **预防**：界面底部直接回显三条任务的状态与 `StartWhenAvailable` 取值，
  让"修复有没有生效"可见，而不是靠猜。
- **补充事实（官方文档）**：错过的任务会被排入队列，**默认延迟 10 分钟**才启动
  （`TaskSettings.StartWhenAvailable` 备注）。所以补跑不是"立刻"，这一点在
  [使用指南](../使用指南.md) 里已说明。
