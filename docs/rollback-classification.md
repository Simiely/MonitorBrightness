# v1.9.0–v1.9.2 改动分类表（含熄灭/唤醒路径标记）

- 日期：2026-11-04
- 基线：`v1.8.5`（用户已验证可用）
- 判据（用户原话）：「这是被报告过的 bug 吗？」「这是用户点名要的吗？」两者都不是 → 未要求的逻辑变更。
- 处理口径：未要求的逻辑变更 → `已回滚` 或 `待用户确认`；没有用户可复现现象、只靠理论推理的"修复" → 一律归入未要求。
- 「触碰熄灭/唤醒」列：`是（直接）` = 改变亮灭命令/时序；`是（旁路）` = 不直接发命令但会影响状态或触发；`否` = 无关。

## 一、总表

| # | 改动 | 版本 | 归类 | 证据 | 触碰熄灭/唤醒 | 处理 |
|---:|---|---|---|---|---|---|
| 1 | `screen_off_method` 默认 `ddc` → `system`（SETTINGS `default`） | 1.9.0 | **未要求的逻辑变更** | 无用户报告「熄灭按钮没反应」；仅 v1.9.0 CHANGELOG「稳定性」自述；闸门跳过是理论推断 | 是（直接） | **已回滚**：默认恢复 `ddc` |
| 2 | 旧配置 `ddc → system` 自动迁移（`load_config` 的 `_ver < 6` 块） | 1.9.0 | **未要求的逻辑变更** | 同上；无用户要求迁移，且原实现无日志、静默写盘 | 是（直接） | **已回滚**；新增反向恢复：`system` 且 `ddc_allow_undeclared=False` → `ddc`，写 `events.log`「system → ddc」后落盘 |
| 3 | 点亮 `mouse_event` 1px → `SetThreadExecutionState(ES_DISPLAY_REQUIRED)` | 1.9.0 | **未要求的逻辑变更** | 无用户报告「点亮会干扰全屏/远程」；纯理论输入副作用 | 是（直接） | **已回滚**：恢复 `_nudge_user_input`，`screen_on_system`/`_wake_one` 两处调用点一并恢复 |
| 4 | 唤醒句柄「谁用谁销毁」+ 全局 30s deadline（`screen_wake_many`） | 1.9.0 | **未要求的逻辑变更** | 无用户复现 use-after-free；CHANGELOG「复核清单」自称必须修 | 是（直接） | **已回滚**：恢复非 daemon 线程 + 每线程 `join(30)` + 统一 `release_targets(targets)` |
| 5 | 能力串/亮度上限缓存 key 改 `DEV#EDID`（`_display_key`/`_display_uid`/`_capabilities_cached`/`_luminance_max`/`monitor_model`/`screen_off_many`/`_switch_input_roundtrip`） | 1.9.0 | **用户明确要求（本轮点名保留）** | 用户原话：「保住已经确认的 bug 修复（…缓存 key）」 | 是（读路径） | **保留** |
| 6 | `_fetch_models` 线程自己 `DestroyPhysicalMonitor` + 20s join | 1.9.0 | **未要求的逻辑变更**（与 #4 同类，但不在唤醒路径） | 无用户复现；纯理论 UAF | 否（型号读取） | **待用户确认**（本轮未回滚；缓存 key 部分随 #5 保留） |
| 7 | 启动读任务 XML、比对路径/参数/触发点，不一致则静默重建；优先 `Set-ScheduledTask` 原地更新 | 1.9.0 | **bug 修复（用户可复现）** | 用户「定时没准时执行」；CHANGELOG 根因 `LastTaskResult 0x80070002`、旧实现先删后建重置 `LastRunTime` | 否 | 保留 |
| 8 | 新增 `--ensure-schedule` 无界面自愈（`cli_ensure_schedule`/`main` 分派） | 1.9.0 | **bug 修复配套** | 同上：换 exe 目录后任务仍指向旧路径 | 否 | 保留 |
| 9 | 时间段增删改立即落盘 + 500ms 防抖重写任务（`SchedulePanel._notify_changed`/`App.on_slots_changed`/`App._register_after_edit`） | 1.9.0 | **bug 修复（用户可复现）** | 用户「定时没准时执行」；旧版只有开关切换才保存，改时间后触发器仍旧 | 否 | 保留 |
| 10 | 任务状态行显示下次/上次/上次结果（`query_task_runtime`/`build_runtime_query_script`/`_runtime_fields`/`_task_result_text`/`task_summary`/`App.refresh_task_status`/`_apply_task_status`） | 1.9.0 | **bug 修复配套（失败不再沉默）** | 同上；旧版失败无提示 | 否 | 保留 |
| 11 | 启动补检：今天最后一档错过 ≤12h 且 `last_fired` 不是今天该档 → **补执行**（`catch_up_decision`/`App._catch_up_worker`/`_finish_auto_checks`/`CATCH_UP_LIMIT_MIN=720`） | 1.9.0 | **用户明确要求，但语义有偏差**（用户说「检查一次」，实现成「补执行」） | 用户原话：「打开程序就自动检查一次」；CHANGELOG 写「补执行一次」 | 是（旁路，可触发熄灭） | **待用户确认**（本轮未改；结论见 §二.3） |
| 12 | `apply_slot` 拆出 + `mark_fired`/`save_config` 记账；`apply_schedule` 改为先取 `active_slot_dict` 再调 `apply_slot` | 1.9.0 | **补检配套** | 同上；`apply_slot` 内会调 `screen_off_cfg`/`screen_on_cfg` | 是（旁路） | **待用户确认**（随 #11） |
| 13 | `last_fired` 新格式（日期+档位+秒级时间戳）：`mark_fired`/`_coerce_fired`/`_fired_newer`/`_read_disk_last_fired`/`save_config` 取磁盘较新值 | 1.9.0 | **补检配套** | 同上；防计划任务与补检重复执行 | 是（旁路，状态） | **待用户确认**（随 #11） |
| 14 | 事件日志 `events.log` + 界面事件行（`_write_event_log`/`read_recent_events`/`App._refresh_events`/`SchedulePanel.set_events`/`EVENTS_FILE`/`_EVENT_LOCK`） | 1.9.0 | **未要求的增强（非操作逻辑）** | 无用户要求；CHANGELOG 自行新增 | 否 | **待用户确认**（未回滚；不改变默认值/流程） |
| 15 | 「添加时间段」按钮不再跟随定时开关置灰（`SchedulePanel.render`/`set_actions_enabled`/`add_row`/`App._fit_window_now`） | 1.9.0 | **bug 修复（用户可复现）** | 用户「添加按钮没反应」；禁用时自绘 Button 静默 return | 否 | 保留 |
| 16 | 时间控件可键入 `HH:MM` + ▲▼ 步进 + 常用预设（`TimeField`/`normalize_time_input`/`shift_time`/`SlotEditor`/`HelpSheet`/`_coerce_slots`） | 1.9.0 | **用户明确要求** | 用户「左右滑块不好用」 | 否 | 保留 |
| 17 | DPI per-monitor v2 + `ScrollArea` 按 `rcWork` 限高（`enable_dpi_awareness`/`_work_area_height`/`ScrollArea`） | 1.9.0 | **未要求的逻辑变更**（理论显示问题） | 无用户报告缩放异常；CHANGELOG「复核清单」 | 否 | **待用户确认**（未回滚） |
| 18 | `config.json` 原子写（`_atomic_write_text`/`save_config`） | 1.9.0 | **用户明确要求（本轮点名保留）** | 用户原话「保住…config 原子写」 | 否 | 保留 |
| 19 | 坏配置备份 `config.json.bad-*.json` + 界面显式提示（`_backup_bad_config`/`App._warn_config_error`/`CONFIG_LOAD_ERROR`） | 1.9.0 | **用户明确要求（本轮点名保留）** | 用户原话「保住…静默异常吞掉」；对应「读失败不再静默」 | 否 | 保留 |
| 20 | Pillow 守卫 import（缺 Pillow 时 CLI 可用、GUI 提示退出） | 1.9.0 | **用户明确要求（本轮点名保留）** | 用户原话「保住…Pillow 守卫」 | 否 | 保留 |
| 21 | 亮灭判定新增 `woke_by_input` 推断（`_screen_woke_by_input`/`_idle_seconds`/`_human_gap`/`_tick_ms`；`_render_states` 命中时 `mark_screen_state`+`save_config`） | 1.9.0 | **未要求的增强** | 无用户报告状态误判；纯状态展示推断 | 是（旁路，仅记账） | **待用户确认**（未回滚） |
| 22 | `CONFIG_VERSION` 5 → 6 | 1.9.0 | **迁移配套（迁移已撤）** | 文件已按 6 落盘；回退编号无收益 | 否 | 保留编号；不再承载迁移 |
| 23 | `parse_hhmm` 返回 int 被当 tuple 解包 → `TypeError`；新增 `report_callback_exception` → `error.log` + 状态栏（`App._report_tk_error`/`ERROR_FILE`） | 1.9.1 | **bug 修复（用户可复现）** | 用户「点添加时间段仍无弹窗，窗口像没反应」；`TypeError: cannot unpack non-iterable int object` 可复现；windowed 下 stderr=None 被吞 | 否 | 保留 |
| 24 | 定时状态行新增「应用」按钮（`SchedulePanel.__init__`/`set_actions_enabled`/`App.apply_schedule`） | 1.9.2 | **用户明确要求** | 上一轮任务：手动重建计划任务要有入口/反馈 | 否 | 保留 |
| 25 | 重建成功确认弹窗（`DarkBox.shownotice`/`_rebuild_report`/`_rebuild_changes`/`_task_info_next`/`App._announce_rebuilt`/`App._finish_apply`/`_finish_register`/`_saved_summary` 删除） | 1.9.2 | **用户明确要求** | 上一轮任务：成功要有明确弹窗、点「确认」关闭 | 否 | 保留 |
| 26 | 启动自动重建不弹窗（`App._finish_auto_checks` 注释/分支；`ensure_task_registration` 事件日志加档位/下次运行） | 1.9.2 | **用户明确要求** | 上一轮任务：自动重建不要一开机弹窗糊脸 | 否 | 保留 |
| 27 | 弹窗正文含档位数/下次运行/路径或触发点变更（`_task_info_next`/`_rebuild_changes`/`_rebuild_report`） | 1.9.2 | **用户明确要求** | 上一轮任务验收条款 | 否 | 保留 |
| 28 | 测试与截图脚本（22 项单测、PostMessage/VK_LBUTTON 取证、三张截图） | 1.9.1–1.9.2 | **验证产物** | 各轮验收要求 | 否 | 保留 |

## 二、三项待定结论（本轮只给结论，不动代码）

### 二.1 点亮：`mouse_event` vs `SetThreadExecutionState(ES_DISPLAY_REQUIRED)`

| 维度 | `mouse_event` 1px（v1.8.5） | `ES_DISPLAY_REQUIRED`（v1.9.0） |
|---|---|---|
| 是否产生输入事件 | 是（真实鼠标移动） | 否 |
| 是否重置显示器空闲计时器 | 是 | 是（微软文档：reset display idle timer） |
| 是否进入 `GetLastInputInfo` | 是（会被"用户是否在操作"判定看到） | 否 |
| 能否把系统从"显示器关闭"踢回有显示 | 实测可用（v1.8.5 基线） | 可能有效，但**没有用户验证记录** |
| 副作用 | 全屏游戏/演示/远控可感知 1px；屏幕边缘可能抖动 | 无输入副作用，但线程结束后状态即失效 |

结论：**1px 移动不是"唤醒的必要一环"的唯一实现，但它是 v1.8.5 验证过的时序里的一环**；`ES_DISPLAY_REQUIRED` 只重置显示空闲计时器，不产生输入事件，语义不同。回滚到 `mouse_event` **不会让唤醒失败率上升**——它就是把已验证路径原样放回去；理论上的失败率变化没有用户数据支撑。已回滚。

### 二.2 启动补检：只检查 vs 补执行

| 语义 | 实际后果 |
|---|---|
| **只检查**（读任务 XML/config/显示器状态，不写） | 安全；开机不会改亮度、不会熄屏；用户能看到"任务是否正常" |
| **补执行**（`apply_slot` 套用今天错过的那一档） | 会写亮度；若该档 `screen="off"`，会调用 `screen_off_cfg` **真的熄屏**；还会写 `last_fired`/config |

12 小时阈值（`CATCH_UP_LIMIT_MIN=720`）的实际边界：
- 只看**今天**已到点的档位，取最后一档；错过 ≤720 分钟才补。
- 因此「开机擅自熄屏」**确实可能**：例如今天 08:30 有一档 `screen=off`，20:29 开机 → 错过 719 分钟 → 补执行 → 熄屏；22:35 有 off 档、23:00 开机同理。
- 不会补"昨天 21:00"的档（那不是今天的档），所以"前一晚关机、次日早上开机"不会被补熄屏；补的是**当天较早的档**。
- 结论：`只检查` 不会擅自熄屏；`补执行` 会。当前实现是补执行，**待用户确认**是否改成只检查（本轮未改）。

## 三、回滚后的屏幕路径验证（不碰显示器）

方法：按函数逐行 diff v1.8.5 ↔ v1.9.3；写操作全部打桩干跑。

| 函数 | 与 v1.8.5 差异 |
|---|---|
| `_broadcast_monitor_power` / `screen_off_system` / `screen_on_system` / `_nudge_user_input` / `_wake_one` / `_output_recycle` / `screen_off_cfg` / `screen_on_cfg` / `wake_options` / `mark_screen_state` / `_write_vcp` / `_read_vcp` / `enumerate_targets` / `release_targets` / `_select` / `screen_off_many` / `screen_wake_many` | **逐字节一致** |
| `_switch_input_roundtrip` / `_capabilities_cached` / `_luminance_max` / `monitor_model` | 仅缓存 key（`DEV#EDID`）不同——用户点名保留 |
| GUI `screen_off` / `_finish_screen_off` / `screen_on` / `_finish_screen_on` / `run_bg` / `_poll_read_queue` | **逐字节一致** |

干跑（ddc 写 `0xD6=2`、system 广播、mouse_event 全部打桩，不改变任何屏幕）：

| 动作 | v1.9.3 调用序列 | v1.9.3 应用侧耗时 | v1.8.5 调用序列 | v1.8.5 应用侧耗时 |
|---|---|---|---:|---|---:|
| ddc 熄灭（2 台） | `0xD6=2` ×2 | 1.4 ms | 同 | 1.7 ms |
| system 熄灭 | `SC_MONITORPOWER 2` 广播 ×1 | 0.6 ms | 同 | 0.5 ms |
| ddc 点亮 | 电源/亮度/输入/切源阶梯 | ~4.0 s（含固定 0.35/0.8/2 s 等待） | 同 | 同 |
| system 点亮 | 广播 + 1px 输入 | ~0.2 s | 同 | 同 |

> 说明：ddc 熄灭的应用侧只有"每台一次 VCP 写"，没有广播等待、没有 join；`system` 广播的 `SendMessageTimeoutW(..., 2000)` 是 v1.8.5 原有的，未改。面板自身从收到 `0xD6=2` 到背光熄灭的延迟由显示器固件决定，程序无法承诺具体毫秒数。

## 四、本轮没有做的事

- 没有按 `docs/architecture-review.md` 改任何代码（报告只留档）。
- 没有改 `catch_up_decision` / 12 小时阈值（待用户确认）。
- 没有改 `_fetch_models` 的句柄生命周期（待用户确认）。
- 没有覆盖 `D:\Tool\MonitorBrightness.exe`；v1.9.3 构建产物在独立目录。
