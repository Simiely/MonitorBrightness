# 17 条链路走查报告（第 1 轮）

> 方法：scenario-walkthrough v3。素材见 `scenarios.md`，能力清单见 `capabilities.md`。
> 日期：2026-11-27。基线：v2.00.1 本地 HEAD。

## 总体结论

17 条素材链路，逐条读代码走查（深度链 10 条自己读原文验证），发现 **4 个真缺陷 / 3 个 UX / 5 个潜在风险 / 1 个素材边界**。
核心判断：**你感觉"一直在打补丁"是对的**——根因不是每个 bug 本身，而是**同一职责有多个写入者/多个入口/多套账**，每次只在出事的那条路径上补一下，另一条路径留着。详见"根因诊断"。

## 问题清单（按风险分 L×I 排序）

| 编号 | 级别 | 问题 | 位置 | 受影响 S-ID | 风险分 | 影响 |
|---|---|---|---|---|---|---|
| P-01 | 🔴 | **托盘注册任务失败后 `enabled=False` 只改内存不落盘**：面板入口 `_finish_register` 有 `save_config`，托盘入口 `_finish_tray_register` 没有。失败后磁盘 config 仍说"已启用"，但任务不存在；下次启动自愈据此认为该注册 | `monitor_brightness.py:5482-5491` vs `:5688-5701` | S-05,S-08 | 5×5=**25** | 定时状态与实际任务不一致，用户以为定时生效 |
| P-02 | 🔴 | **注销任务失败不恢复 `enabled`**：`cancel_schedule:5704` 先把 `enabled=False` 落盘再注销；`_finish_unregister:5718` 失败只提示，磁盘已是 False 但任务还在 → 定时任务会继续执行一个"已关闭"的配置 | `:5704-5709`、`:5718-5723` | S-05,S-08 | 5×5=**25** | 用户关掉定时后屏幕仍被定时熄灭 |
| P-03 | 🔴 | **屏幕状态"键鼠唤醒"只改内存不落盘**：`_render_states:5201` 调 `mark_screen_state(cfg,"on")` 但不 `save_config`；GUI 状态卡说"亮着"，下次 CLI 进程读磁盘仍是"off" | `:5198-5201`（注释明确承认"只做内存记账"） | S-06,S-14 | 4×4=**16** | GUI 与 CLI 对同一时刻亮灭结论相反 |
| P-04 | 🔴 | **6 个任务注册入口失败回滚策略四种不同**：面板回滚落盘、托盘回滚不落盘、改档位不回滚、自检不回滚、应用不回滚、注销不恢复 | C-36 六处（`:5479/:5560/:5602/:5644/:5664` + 自检） | S-05,S-08,S-16 | 4×5=**20** | 同一个"启用定时"动作，从哪点失败结果不同 |
| P-05 | 🟡 | **GUI「应用」与 CLI `--apply-schedule` 同名不同义**：模块级 `apply_schedule:2385`=套档位；`App.apply_schedule:5647`=重建任务。读代码的人（和 AI）极易混淆，本轮走查的子代理也踩了 | `:2385` vs `:5647` | S-13 | 4×3=12 | 维护误改风险，历史上已引发过 `_finish_apply` 重名事故 |
| P-06 | 🟡 | **拓扑模式部分断开时结论与单屏直觉不符**：两屏断一屏，`topology_state_for` 报 `ACTIVE=1/2 → on`，状态卡说"显示中"，但用户刚断的那块是灭的；同时 `wake_options.expect_off=False` 仍读旧账 | `:1265-1272`、`:1288-1305`、`:2379-2382` | S-04,S-14 | 4×3=12 | 用户困惑"我明明灭了一块屏却说亮着" |
| P-07 | 🟡 | **CLI 与 GUI 屏幕目标不同源**：`cli_screen` 从磁盘 `resolve_monitors(cfg)`，GUI 从内存 `selected_indices()`。GUI 勾选未保存时 CLI 用的是旧选择 | `:5922-5925` vs `:5274-5277` | S-14 | 3×4=12 | 定时/CLI 熄错屏 |
| P-08 | 🟡 | **`topology_enable_anchor` 传空 indices**：`screen_on_cfg(cfg, [])` 里 `[]` 与恢复出的锚点下标取并集，当前单快照场景恰好只写目标屏；但语义是"全部"，多快照/扩展场景下会放大 | `:1673`、`:1997-2013` | S-10 | 3×3=9 | 潜在：多屏时启用一块可能改所有屏亮度 |
| P-09 | ⚪ | **`active_slot` vs `active_slot_dict` 两套判档**：乱序/重复分钟/跨午夜实测一致，但两套代码各自回绕（`:2265` vs `:2281`），未来改一处会漂移 | `:2255-2282` | S-13,S-17 | 2×3=6 | 低，当前无行为差异 |
| P-10 | ⚪ | **`monitor_brightness_qt.py` 旧实现共用 config.json**：老 schema（`monitor/day_time/night_level`），若被误运行会覆盖新配置并注册旧任务名 | `monitor_brightness_qt.py:29-32,36,53` | — | 2×4=8 | 低，但需确认不分发 |
| P-11 | ⚪ | **`_refresh_task_status = refresh_task_status` 双名**：兼容别名，两个名字混用 | `:5747` | S-16 | 2×2=4 | 低 |
| P-12 | ⚪ | **`_ccd_save_snapshot` 单文件覆盖 vs 探针脚本时间戳多文件**：用户用脚本 `--on` 恢复后，程序读到的是自己那份，两份快照可能不一致 | `:1361` vs `scripts/topology_switch.py:42` | S-07 | 2×3=6 | 低 |
| P-13 | ⚪ | **`load_config` 反向恢复判别式**：`system and not ddc_allow_undeclared` 会把"用户主动选 system 且没确认 DDC 风险"的配置改回 ddc | `:438-444` | S-09 | 3×3=9 | 低-中，用户主动选择被覆盖 |
| P-14 | ⚪ | **亮度文案重复计算**：`_render_states:5203-5212` 与 `_update_hint_brightness:5218-5227` 各写一遍 | — | S-06 | 2×2=4 | 低 |
| P-15 | ⚪ | **`_finish_screen_off` 把自动复亮 Timer 塞进热键队列**：语义混用，但功能正常 | `:5302-5303` | S-14 | 2×2=4 | 低 |
| P-16 | ⚪ | **`_capabilities` 等 12 处静默吞异常**：设计取舍（只读探测不拖垮程序），但缺日志 | 见 capabilities.md §8.5 | — | 2×2=4 | 低 |
| P-17 | 素材边界 | 权限差异/离线列全空：单机单用户桌面工具，无多角色/云端，属合理留空 | `scenarios.md` 矩阵 | — | — | 无 |

## 覆盖矩阵结论

盲区已补：空态列原只有 S-02，本轮明确 S-07（无快照）/S-08（注册失败）/S-09（旧配置）覆盖三处空态/故障。
权限差异与离线两列留空并有理由（单机工具）。L4-L9 各至少一条深度链。✅

## 走查验证的良好点（程序扎实的地方，证明不是空谈）

1. **硬件红线执行到位**：全仓无 `0xD6=4/5` 写入；`0xD6=2` 有 `ddc_allow_undeclared` 确认门（`:1891-1920`）。
2. **拓扑动作快照优先**：`topology_off:1609-1613` 快照写失败绝不碰拓扑；回读失败恰好回滚一次（`:1632-1637`），无无限重试。
3. **ctypes 签名显式声明**：`enumerate_targets:653-711` 避免 64 位 HANDLE 截断，`--diag` 只读路径稳定。
4. **配置原子写**：`_atomic_write_text:467-481` 同目录 mkstemp + os.replace，不会读到半个文件。
5. **忙时排队只补一次**：`_queue_screen_action:5242` 明确保留最后一次意图，`_dispatch_pending_screen:5262` 消费后置 None，无重复执行。
6. **单实例排除 CLI**：`acquire_single_instance` 在 CLI 路径跳过（`:5865`），定时任务不会被托盘实例拦死。

## 结论三问

1. **链路逻辑通顺吗？** 主流程通顺（17 条中 13 条走通），但"失败分支"和"状态归属"系统性薄弱。
2. **是补丁叠补丁吗？** **是。** 最典型是任务注册 6 入口（P-01/P-02/P-04）、屏幕状态 5 源（P-03/P-06/P-07）、配置反向恢复（P-13）。每次只补出事的那条路径。
3. **会无法操作/出 bug 吗？** 会。P-02 会导致"关了定时还继续熄屏"，P-01/P-03 会导致状态显示与事实相反。

## 根因诊断（回答用户"修不到真正的地方"）

**根因：没有"单一事实源"（SSOT）+ 写入路径发散。**

| 职责 | 应该的归属 | 实际的写入者 | 后果 |
|---|---|---|---|
| 定时 enabled 状态 | `save_schedule`/`cancel_schedule` 单一入口 + 失败统一回滚 | 6 个入口各自 `save_config` + 4 种失败处理 | P-01/P-02/P-04 |
| 屏幕亮灭状态 | `screen_verdict` 单一判定 + `mark_screen_state` 单一落盘 | 磁盘账 + 拓扑 ACTIVE + DDC 回读 + 内存账 4 套 | P-03/P-06/P-07 |
| 档位判定 | `active_slot_dict` 单一实现 | `active_slot` 与 `active_slot_dict` 两套 | P-09（潜在） |
| 任务注册 | `ensure_task_registration` 单一入口（含自愈比对） | 3 处直接 `register_tasks` 绕过 | P-04 |

**之前几次修复为什么像打补丁**：`_finish_apply` 重名、接回亮度漏写、断开屏不显示——每次都是"某条路径的某个回调/参数错了"，改的是**那条路径**；但产生这些错误的共同原因是**同一职责被复制到多条路径**，每条各自演化、各自补。只改出事那条，下一条迟早再犯。

## 建议处理（按风险分排序）

本轮先修 🔴 四项（P-01~P-04）+ 降低 P-05/P-06 维护风险的低成本项，其余进残余风险清单跟踪。
修复原则：**收敛到单点归属**，不是再加 if 特判。

- P-01/P-02/P-04 → 抽 `App._set_schedule_enabled(enabled, save=True)` 统一"写磁盘 + 改开关 + 失败回滚"，6 个入口都调它；失败回滚只在一处实现。
- P-03 → `mark_screen_state` 增加 `persist` 参数由调用方显式决定；探测路径用 `persist=False` 但补一次延迟落盘（或明确文档化"内存账不跨进程"），消除 GUI/CLI 分歧。
- P-05 → `App.apply_schedule` 改名 `rebuild_schedule_tasks`，CLI 模块级保持 `apply_schedule`。
- P-06 → 拓扑模式部分断开时，状态结论改为分屏明细（`ACTIVE=x/y，已断开 z 块`），不说笼统的"显示中"。

## 第 1 轮修复闭环（用户确认「修 4 个🔴 + 收敛单点」后执行）

| 编号 | 修复 | 证据 |
|---|---|---|
| P-01 | 托盘注册失败改为 `_rollback_schedule` → `_set_schedule_enabled(persist=True)` | `WalkthroughFixTest.test_tray_register_failure_uses_unified_rollback` |
| P-02 | ① 注销失败恢复 `enabled=True` 并落盘；② **补深层护栏**：`cli_apply_schedule()` 执行前再查磁盘 `enabled`，false 则拒绝执行（走查时新发现：注销失败残留任务会照常熄屏，即使 enabled=false） | `test_cancel_failure_restores_enabled`、`test_cli_apply_schedule_skips_when_disabled` |
| P-03 | 探测发现键鼠唤醒后 `mark_screen_state` + `save_config` 落盘 | `test_wake_detection_persists_state` |
| P-04 | 5 个失败回调统一走 `_rollback_schedule`；`cfg["enabled"]` 直接赋值收敛到 `_set_schedule_enabled` 一处 | 回归重走：5/5 回调含 `_rollback_schedule`；全仓直接赋值仅剩 2 处（均在单点函数内） |
| P-05 | `App.apply_schedule` 改名 `rebuild_schedule_tasks`，GUI「应用」按钮同步 | `test_rebuild_name_is_distinct_from_module_apply` |

**回归重走**：69 tests OK（原 62 + 7 项走查回归）；REPO CHECK PASS；
S-05/S-08 六个入口失败路径 5/5 统一、S-13 判档一致、enabled=True 时定时正常执行（用户已验收路径零改动）；S-01/S-02/S-04/S-07/S-10 等冒烟沿用原有 62 项全绿。

## 根因收敛验证

| 职责 | 修复前写入者 | 修复后 |
|---|---|---|
| 定时 enabled 状态 | 6 入口各自写，4 种失败处理 | `_set_schedule_enabled` 单点写入；`_rollback_schedule` 单点回滚 |
| 屏幕状态账 | 内存账（GUI）与磁盘账（CLI）分叉 | 探测发现唤醒也落盘，两进程一致 |
| 屏幕状态判定命名 | `App.apply_schedule` 与模块 `apply_schedule` 同名 | 改名 `rebuild_schedule_tasks` |

## 停止准则检查（第 1 轮末）

- 🔴 **已清零**（4→0，含走查中新发现的 P-02 深层护栏）。
- 本轮新发现 17 项；修复后残余 13 项均为 🟡/⚪（见下），未达"连续 2 轮递减"→ **还需第 2 轮**。
- 回归全绿（69 tests + REPO CHECK + 冒烟）。
- 探索会话：未开，第 2 轮补。按 skill 强制停止条件，轮次上限 3，下一轮收敛后收尾。
