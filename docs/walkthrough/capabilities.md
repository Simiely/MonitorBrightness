# 场景走查 · 能力清单（步骤 1）

> 方法来源：`workbuddy-skills/skills/scenario-walkthrough/SKILL.md` v3。
> 生成日期：2026-11-27。基线：v2.00.1（main，本地领先 origin 10 提交）。
> 铁律：素材里出现本清单外的功能 = 素材错误。
>
> ⚠️ 行号是 v2.00.1 的，已被后续版本改动；新能力见
> `report.md` 第 2 轮报告（S-31~S-39：恢复双屏 / 首次检测 / 配置迁移 /
> 悬停说明 / 交接候选 / VDD 名称）。

主实现：`monitor_brightness.py`（5995 行）。所有行号默认指该文件。

## 1. 配置与状态底座

| C-ID | 能力 | 入口 | 状态/数据 | 关键位置 |
|---|---|---|---|---|
| C-01 | 设置项唯一声明表 + 类型规整 | 内部 | `SETTINGS` 18 键 | `SETTINGS:159-196`、`coerce_setting:200-246` |
| C-02 | 配置加载与迁移 | 启动 / 每个 CLI | `config.json` | `load_config:411-464`（含反向恢复 `:438-461`） |
| C-03 | 原子保存 | 所有设置出口 | `version=6` | `save_config:484-495`、`_atomic_write_text:467-481` |
| C-04 | 显示器三层身份 | GUI 勾选 / 保存定时 | `monitors`/`monitor_ids`/`monitor_anchors` | `resolve_monitors:2325-2356`、`selection_anchors:2296-2322`、`selection_payload:2359-2376` |
| C-05 | 屏幕状态软件记账 | 所有熄/亮 | `screen_state`/`screen_state_at`/`screen_off_tick` | `mark_screen_state:1034-1041` |
| C-06 | 旁路日志 | 旁路 | `events.log`/`error.log` | `_write_event_log:323-341`、`_report_tk_error:5509-5526` |

## 2. 亮度调节

| C-ID | 能力 | 入口 | 关键位置 |
|---|---|---|---|
| C-07 | 唯一显示器枚举源 | 内部 + `--list` | `enumerate_targets:653-711` |
| C-08 | 批量写亮度 | 滑块/预设/CLI/定时 | `set_brightness_many:916-933` |
| C-09 | 批量读亮度 | 刷新/检测/`--probe` | `get_brightness_many:936-955` |
| C-10 | CLI 立即调亮度 | `--set` | `cli_set:3099-3115`（严格解析） |
| C-11 | 滑块 + 三预设 | GUI | `apply_now:5115-5122`、`apply_preset:5136-5146` |
| C-12 | N 档判档（两套实现） | 定时/自检 | `active_slot:2255`、`active_slot_dict:2269` |
| C-13 | 档位自检 | `--selftest` | `cli_selftest:3260-3271` |

## 3. 熄屏 / 点亮

统一分派 `screen_off_cfg:1982-1994` / `screen_on_cfg:1997-2013` / `screen_wake_entry:2202-2220`。
硬件红线：绝不写 `0xD6=4/5`；`0xD6=2` 需确认。

| C-ID | 能力 | 关键位置 |
|---|---|---|
| C-14 | system 整组熄/亮 | `screen_off_system:1762`、`screen_on_system:1767` |
| C-15 | ddc 熄灭（0xD6=2） | `screen_off_many:1891-1920` |
| C-16 | ddc 分级点亮 | `_wake_one:1817-1877`、`screen_wake_many:1923-1964` |
| C-17 | 0x60 快速点亮 | `screen_wake_flip:2143-2199` |
| C-18 | 点亮统一入口（三重门） | `screen_wake_entry:2202-2220` |
| C-19 | CLI 熄/亮 | `cli_screen:5919-5937` |
| C-20 | 记账落盘 | `mark_screen_state` 6 处调用 |
| C-21 | 熄灭后自动复亮 | `_auto_relight_minutes:5373-5377`、Timer `:5302` |

## 4. 拓扑模式

| C-ID | 能力 | 关键位置 |
|---|---|---|
| C-22 | CCD 只读探测 | `ccd_probe_topology:1142-1244` |
| C-23 | 拓扑状态折叠 | `topology_state_for:1247-1272` |
| C-24 | 亮灭一句话结论（优先级反转） | `screen_verdict:1275-1342` |
| C-25 | 断开计划护栏 | `topology_plan_off:1518-1564` |
| C-26 | 断开 + 回滚 | `topology_off:1582-1643` |
| C-27 | 接回 | `topology_on:1699-1721` |
| C-28 | 接回后写亮度 | `topology_indices_for_anchors:1676-1696` |
| C-29 | GUI 断开屏行 + 启用按钮 | `topology_inactive_rows:1646-1660`、`MonitorPanel.rebuild:4160-4176`、`App.topology_enable:5309-5320` |
| C-30 | 快照（单文件覆盖） | `TOPO_SNAPSHOT_FILE:1361`、`_ccd_save_snapshot:1464-1467` |

## 5. 定时计划

| C-ID | 能力 | 关键位置 |
|---|---|---|
| C-31 | 到点应用档位 | `apply_schedule:2385-2420` |
| C-32 | 任务注册脚本 | `build_register_script:2492-2578` |
| C-33 | 注册/自愈 | `register_tasks:2581`、`ensure_task_registration:2658` |
| C-34 | 任务状态查询双路 | `query_task_status_local:2762`、`query_task_status:2795` |
| C-35 | 重建确认报告 | `_rebuild_report:2902-2927`、`_announce_rebuilt:5667-5674` |
| C-36 | **6 个任务写入入口** | 启动自检/CLI/防抖/开关/应用/托盘 |
| C-37 | 注销 | `cancel_schedule:5704-5709`、`_finish_unregister:5718` |
| C-38 | 状态 → 文案 | `task_summary:5769-5821` |

## 6. GUI / 交互

| C-ID | 能力 | 关键位置 |
|---|---|---|
| C-39 | 异步编排 | `run_bg:4998`、`_poll_read_queue:5014` |
| C-40 | 忙时排队 | `_queue_screen_action:5242`、`_dispatch_pending_screen:5262` |
| C-41 | 显示器勾选 | `MonitorPanel:4098-4186` |
| C-42 | 状态卡 | `StatusCard:3996-4095` |
| C-43 | 定时面板 | `SchedulePanel:4418-4606` |
| C-44 | 设置/说明 | `SettingsSheet:4609-4680`、`apply_off_method:4905-4933` |
| C-45 | 热键 | `_start_hotkey_listener:5387-5411` |
| C-46 | 托盘 | `_create_tray:5450-5464` |
| C-47 | 单实例 + 自启 | `acquire_single_instance:5871-5891` |

## 7. 日志 / 诊断

| C-ID | 能力 | 关键位置 |
|---|---|---|
| C-48 | `--diag` 五段只读报告 | `cli_diag:3169-3244` |
| C-49 | `--probe` | `cli_probe:3151-3166` |
| C-50 | `--selftest`/`--list`/`--ensure-schedule` | `:3260`/`:3133`/`:3247` |
| C-51 | 事件日志 | `_write_event_log:323` |
| C-52 | 错误日志 | `_report_tk_error:5509` |

## 8. 状态机（走查重点）

- **屏幕状态 5 源并存**：`cfg.screen_state`（磁盘）/ 拓扑 ACTIVE / DDC 回读 / `_FAST_FLIP_AT` / `App._last_verdict`。
- **忙时排队**：`_busy` → `_pending_screen`（只留最后一次）→ 完成后补执行一次。
- **定时启用/禁用 6 入口 4 种失败处理**：回滚策略互不相同。
- **拓扑断开/接回**：快照→提交→回读→一次回滚；接回→恢复→回读→写亮度。
- **失败分支**：静默吞掉（约 12 处）/ 弹窗 / 回滚兜底 / 仅状态栏。

## 9. 结构性问题候选（走查输入）

| # | 类型 | 怀疑点 |
|---|---|---|
| S-01 | 整份重复实现 | `monitor_brightness_qt.py` 自带旧 schema 配置读写，共用同一 config.json |
| S-02 | 同名不同义 | 模块级 `apply_schedule`（套档）vs `App.apply_schedule`（重建任务） |
| S-03 | 别名 | `_refresh_task_status = refresh_task_status` 两个名字混用 |
| S-04 | 迁移缺失 | D5 承诺"首次保存迁移 monitor_anchors"，`load_config` 无迁移块 |
| S-05 | 同职责两套实现 | `active_slot` vs `active_slot_dict` 回绕写法不同 |
| S-06 | 同状态多写者 | 19 处 `save_config`，内存 cfg 与磁盘互覆盖 |
| S-07 | 同动作多入口 | 注册任务 6 入口，3 处直接 register 绕过自愈比对 |
| S-08 | 同状态三套账 | 拓扑 ACTIVE / `screen_state` / `_status_payload` 三处口径 |
| S-09 | 同动作传参不同 | `topology_enable_anchor` 传 `[]` = 全部屏，不是目标锚点那台 |
| S-10 | 两套快照命名 | 主程序单文件 vs 脚本时间戳多文件 |
| S-11 | 多层嵌套 | 点亮 4 层嵌套 + 三次回退 |
| S-12 | 同职责两处选源 | `pick_flip_source` vs `_switch_input_roundtrip` 排序口径不同 |
| S-13 | 补丁叠补丁 | `load_config` 反向恢复；`ddc_allow_undeclared` 三次变更 |
| S-14 | 补丁叠补丁 | `allow_recycle` / 三重门 / 排队 / Timer 塞热键队列 |
| S-15 | 补丁叠补丁 | `build_register_script` legacy 清理 + Set 优先 + 回退 + CATCHUP 独立 try |
| S-16 | 文案重复计算 | 亮度/亮灭文案各写 2-3 遍 |
| S-17 | GUI 直调设备层 | 8 处直接调 DDC，无服务层 |
| S-18 | 未受控全局缓存 | `_CAPS_CACHE`/`_MAX_LUM`/`_EDID_CACHE` 无锁 |
