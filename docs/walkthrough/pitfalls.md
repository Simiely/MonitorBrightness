# 走查坑库

> 格式：现象 / 原因 / 解决 / 预防 / 相关。状态：`open → fixed → verified`。

## P-01 · 托盘注册失败不落盘 ✅ verified

- **现象**：从托盘开启定时 → 注册失败 → 界面开关拨回 OFF，但磁盘 `config.json` 仍是 `enabled=true`。
- **原因**：`_finish_tray_register` 只改内存 `cfg["enabled"]`，面板入口 `_finish_register` 有 `save_config`，两处实现分叉。
- **解决**：统一走 `App._set_schedule_enabled(enabled, persist=True)` 单点写入 + `_rollback_schedule` 单点回滚。
- **预防**：新增回归测试断言失败后磁盘配置为 false；走查矩阵 S-05/S-08 覆盖。
- **相关**：`monitor_brightness.py` `_set_schedule_enabled:5710`、`_rollback_schedule:5726`。

## P-02 · 注销失败残留任务继续熄屏 ✅ verified

- **现象**：关闭定时 → 注销任务失败 → 磁盘 `enabled=false` 但任务仍在 → 到点照样熄屏。
- **原因**：① `cancel_schedule` 先落盘 `enabled=False` 再注销，失败后 `_finish_unregister` 不恢复；② `cli_apply_schedule()` 执行时不查 `enabled`，残留任务无条件执行。
- **解决**：① 注销失败走 `_rollback_schedule(True)` 恢复开关并落盘；② `cli_apply_schedule` 执行前检查磁盘 `enabled`，false 则拒绝执行（双保险）。
- **预防**：两项回归测试；启用/注销状态机只走单点写入函数。
- **相关**：`cli_apply_schedule:3118`、`_finish_unregister:5720`。

## P-03 · 屏幕状态账 GUI/CLI 分歧 ✅ verified

- **现象**：GUI 检测到键鼠已唤醒屏幕（状态卡说"亮着"），但 CLI `--screen status` 下次读磁盘仍是"off"。
- **原因**：`_render_states` 发现 `woke_by_input` 后只在内存 `mark_screen_state`，注释明确说"避免探测回调里同步 IO"——但 `save_config` 只写小 JSON、不碰硬件，这个权衡不成立。
- **解决**：探测发现唤醒后 `mark_screen_state` 紧接着 `save_config`。
- **预防**：回归测试断言磁盘配置随探测更新；文档化"屏幕状态只有一份磁盘账"。
- **相关**：`_render_states:5198`。

## P-04 · 6 个任务入口失败回滚策略四种不同 ✅ verified

- **现象**：同一个"启用定时"动作，从面板/托盘/改档位/应用/自检/注销不同入口失败，回滚行为互不相同。
- **原因**：每个入口各自实现"保存 config + 注册 + 提示"，没有单一归属。
- **解决**：`cfg["enabled"]` 直接赋值收敛到 `_set_schedule_enabled` 一处（全仓只剩 2 处：写入与回滚）；5 个失败回调统一走 `_rollback_schedule`。
- **预防**：回归重走断言 5/5 回调含 `_rollback_schedule`、全仓无散落的 `cfg["enabled"]=`。
- **相关**：`_finish_register` / `_finish_tray_register` / `_finish_edit_register` / `_finish_apply` / `_finish_unregister`。

## P-05 · App.apply_schedule 与模块 apply_schedule 同名 ✅ verified

- **现象**：GUI「应用」按钮调 `App.apply_schedule`（重建任务），CLI `--apply-schedule` 调模块 `apply_schedule`（套当前档位）。读代码的人与 AI 都容易混淆，本轮走查的子代理也踩了。
- **原因**：历史命名未区分"重建任务"与"应用档位"两个职责。
- **解决**：GUI 侧改名 `rebuild_schedule_tasks`，按钮引用同步。
- **预防**：回归测试断言 `App` 不再有 `apply_schedule` 属性。
- **相关**：`SchedulePanel:4479`、`App.rebuild_schedule_tasks:5653`。

## 第 2 轮探索会话新增（E1/E2 编号）

### E1-01 · 定时记账把亮度成功当屏幕动作成功 ✅ verified
- **现象**：档位 off 时亮度写成功、熄屏失败 → 状态记 off、任务报成功、屏幕亮着。
- **原因**：`apply_schedule` 用合并后的 `ok`（含亮度成功）判断记不记账。
- **解决**：改用 `sok`（屏幕动作自己的成功）；`cli_apply_schedule` 退出码仍反映合并结果。
- **预防**：回归测试 `test_schedule_state_uses_screen_action_result_only`。

### E1-02 · GUI 陈旧内存覆盖 CLI 新状态 ✅ verified
- **现象**：GUI 开着的几小时里定时任务写了 `screen_state=off`，用户改个无关设置保存 → 状态被打回 on。
- **原因**：`self.cfg` 只在启动时 load 一次；`save_config` 整文件覆盖、无版本合并。
- **解决**：`save_config` 前按 `screen_state_at` 合并磁盘更新的三字段（谁新用谁）。
- **预防**：双向回归测试（磁盘新用磁盘 / 内存新用内存）。

### E1-03/04/05/06 · 轮询链累积 / 热键泄漏 / Timer 残留 / 快照过期
- 前三项已修（`_polling` 护栏、注册失败回滚、timer 句柄可取消）；快照过期保留待用户决策。

### E2-01/02/03 · 缺 Pillow CLI 崩 / caps 类型错丢配置 / models 坏键 GUI 崩 ✅ verified
- 均为"容错边界"类：模块级冗余 import 删除、类型守卫、跳过坏键。三项都有回归测试。

## 根因沉淀（写给未来的自己）

这 5 个坑的共同根因是**同一职责多处写入**：定时开关 6 入口、屏幕状态 5 源、回调重名。
每次只在出事的那条路径补一下，另一条路径迟早再犯。
修复时优先问"这个状态/动作有几个写入者？"，把写入收敛到单点，而不是再加 if 特判。
