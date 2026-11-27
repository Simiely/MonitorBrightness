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

## 第 3 类根因：连"要不要有这份状态"都没问（v2.01.0 沉淀）

前两轮的坑库都在回答"这份状态该由谁写"。用户纠偏后发现问题更靠上一层：
**这份状态本来就不该存在。**

### N-01 · 把"上次操作结果"当成"当前事实" ✅ verified
- **现象**：`screen_state` 记着 off，但用户可能已经按了显示器电源键、或 Windows
  自动熄屏又唤醒；程序仍理直气壮显示"熄灭中"。反过来，DDC 回读在黑屏时报 1，
  程序显示"亮着"——v1.5.0 的熄屏事故就是这么来的。
- **本质**：存"我做了什么"，却拿它回答"现在是什么"。这两个问题不是一回事。
- **解决**：整份删掉。拓扑模式读 CCD ACTIVE（系统事实），其余模式读不到就说
  `unknown`，把可读的寄存器值摊给用户判断。
- **预防**：`test_ddc_path_never_claims_on`、`test_screen_verdict_never_reads_state_bookkeeping`。

### N-02 · 用"恢复历史快照"实现"接回" ✅ verified（本轮最大一项）
- **现象**：断开副屏时把整份 CCD 字节存盘，接回时原样写回。期间用户在 Windows
  设置里调过排布 / 分辨率 / 换过主屏，接回后这些改动全被抹掉。
- **本质**：把"某一刻的完整系统配置"当成"目标配置"。系统是所有人的，不是我的。
- **解决**：现读当前拓扑 → 挑一条空闲 source → 从**当前** modes 数组现算
  `(srcMode<<16)|0xFFFF` 与 `(tgtMode<<16)|desktopMode` → 提交 → 回读。
  真机实测现算值与 Windows 自己填的**逐位一致**。
  快照降级为"本次操作失败时的内存回滚材料"，用完即弃。
- **预防**：`TopologyPlanOnTest` 9 项 + `test_snapshot_helpers_are_memory_only`。

### N-03 · 让"记住的开关"否决"当下的指令" ✅ verified
- **现象**：`--ensure-schedule --force` 在 `enabled=false` 时被静默 return，
  用户显式要求"对齐一次"却什么都没发生。
- **本质**：记忆优先于意志。用户此刻说的话，比上次的勾选更重要。
- **解决**：`force` 定义为"对齐"，两个方向都做。

### N-04 · 危险动作的"一次性授权"被当成永久设置 ✅ verified
- **现象**：`ddc_allow_undeclared` 一旦置 True 就再也不会 False；换显示器后，
  "盲写未声明的 0xD6=2" 这个风险被悄悄继承。
- **本质**：授权是有上下文的（哪块屏、哪种方式），却被存成了全局永久布尔。
- **解决**：离开 ddc 方式即收回授权，下次选择重新确认。

## 判断"这是不是状态账"的三个问句

1. 这个字段存的是**系统的当前事实**，还是**我上次做了什么**？
2. 如果用户在我不知情时改过系统，我还对不对？
3. 把这个字段删掉，功能会真的坏吗？（常常不会——现读往往比记账更准）

> 前两轮修的是"谁来写这份状态"；这一轮修的是"根本不该有这份状态"。
> 后者才是"反反复复修很多遍都是打补丁"的真正原因：补丁修补的是一个不该存在的洞。

## 第 4 类根因：拿假证据改代码（v2.01.1 沉淀）

### N-05 · 日志写在"降级分支"里，却当成"降级发生了" ✅ verified

- **现象**：events.log 里 4 次「拓扑接回：现算 mode 被拒，降级为由 Windows 自选模式」。
  我据此认定"现算 mode 这条路总被 Windows 拒"，把两档顺序反转了。
- **真相**：那句 `_write_event_log` 写在第 1 档的 `else` 分支里，**无条件输出**——
  第 1 档成功也会打出来。现算 mode 其实一直是好的，真正失败的是第 2 档（INVALID）。
- **代价**：顺序反转后，第 1 档变成了注定失败的 INVALID 路径，rc=87 反而更容易出现。
- **规则**：日志必须在**动作结果已知之后**写，且带上真实返回值（`rc=%d`）。
  凡是"提示某人做了降级"的日志，都要能回答"哪一次判断得出要降级"。

### N-06 · 有一个不需要改系统就能验证的手段，却先改代码 ✅ verified

- **现象**：围绕 rc=87 我先后猜了两次根因（未连接组合 / 部分提交），
  两次都改了代码、两次都错。而 `SetDisplayConfig` 支持 `SDC_VALIDATE`(0x40)：
  把 `SDC_APPLY`/`SDC_SAVE_TO_DATABASE` 换成它，Windows 只校验不落地，
  可以在内存里随便模拟"断开 → 接回"，一秒跑十种组合。
- **规则**：动硬件之前先问一句"这个 API 有没有 dry-run / validate 模式？"
  有的话，先把它写成脚本（本项目的 `scripts/topology_repro87.py`），
  让结论变成一张可复跑的表，再动产品代码。
- **附带好处**：那张表直接成了回归测试的依据（`TopologySubmitShapeTest`）。

> 这两条合起来是一件事：**先把"事实"拿到手，再决定改什么。**
> 假日志 + 无验证手段 = 一定会改错，而且会反复改错。

