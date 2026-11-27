# DEVELOPMENT.md · 开发文档

> 给开发者（自己）看的：**架构总览 + 问题索引**。细节都在 `docs/`，主文件只做门面。

## 项目概览

MonitorBrightness 是一个 Windows 桌面小工具，用于**外接显示器亮度的手动 / 定时调节**。

- 通过 **DDC/CI** 直接向显示器下发亮度指令（VCP `0x10`），属**硬件级背光调节**，非软件滤镜，不偏色。
- 定时**不依赖常驻进程**：程序把「时间点 + 亮度 + 屏幕动作」写入 **Windows 任务计划程序**，
  由系统到点调用 exe 的命令行模式执行。
- 单文件交付（`monitor_brightness.py`，tkinter + dxva2），PyInstaller 打成免安装 exe。

## 架构总览

```
GUI(tkinter) ──run_bg(后台线程)──► 亮度/唤醒层：enumerate_targets → dxva2(0x10/0x60/0xD6)
     │                                                      
     └──subprocess──► 定时层：PowerShell Register/Set-ScheduledTask
                                    │ 到点（系统调度）
                                    ▼
                        exe --apply-schedule → active_slot_dict() 判档
                                             → 亮度 + 该档的 screen 动作
```

单文件内按注释分 8 区：配置读写(141) / 显示器与亮度(529) / 档位判定(1588) /
任务计划(1737) / 深色主题(2244) / 命令行(2354) / 图形界面(2573) / 入口(5046)。

完整分层表、真实函数名、GUI 组件树、持久化清单、健康度数字与已知债务见
**[docs/架构.md](docs/架构.md)**。

## 三条不能破的约束

1. **单文件交付** —— 主脚本独立可运行、无外部资源依赖；逻辑模块化，不拆包。
2. **后台线程不碰 Tk** —— 一律 `run_bg(job, on_done, busy)`，由主线程 `after` 轮询取结果。
3. **不用真机屏幕做破坏性测试** —— 熄灭类验证一律 monkeypatch 命令下发。

## 问题索引（一坑一篇，共 13 篇）

模板与 knowledge-base 完全一致：**TL;DR → 现象/问题 → 根因 → 解决 → 预防**，可直接提炼。

| # | 问题 | 关键词 | 文档 |
|---|---|---|---|
| 1 | 深色主题下勾选框看不出勾没勾 | ttk · element_create | [checkbox-invisible-dark-theme](docs/问题记录/checkbox-invisible-dark-theme.md) |
| 2 | 界面「AI 味」重（满屏饱和蓝） | 配色 · 强调色收敛 | [ui-ai-flavor](docs/问题记录/ui-ai-flavor.md) |
| 3 | 重建列表后「当前亮度」目标集错乱 | Tk 虚拟事件同步派发 | [monitor-list-rebuild-race](docs/问题记录/monitor-list-rebuild-race.md) |
| 4 | 任务计划状态一直「读取失败」 | PowerShell 拼行 · 打桩漏测 | [task-status-read-failed](docs/问题记录/task-status-read-failed.md) |
| 5 | 登录校准任务注册不出来 + 点保存没反应 | `-AtLogOn -User` · `--windowed` 吞异常 | [catchup-task-register-denied](docs/问题记录/catchup-task-register-denied.md) |
| 6 | 定时任务在「开机时间已过」后不生效 | StartWhenAvailable · 幂等判档 | [schedule-missed-while-off](docs/问题记录/schedule-missed-while-off.md) |
| 7 | 显示器名称显示成对象 repr | 能力串取 model | [monitor-name-object-repr](docs/问题记录/monitor-name-object-repr.md) |
| 8 | GUI 卡在「当前亮度: 读取中…」 | 跨线程 Tk · 串行阻塞 | [gui-stuck-reading-brightness](docs/问题记录/gui-stuck-reading-brightness.md) |
| 9 | CLI 参数写错会静默把屏幕调黑 | 宽恕式解析 · 严格校验 | [cli-arg-silently-black](docs/问题记录/cli-arg-silently-black.md) |
| 10 | 打包 exe 报 `No module named 'tkinter'` | 精简版 Python | [exe-missing-tkinter](docs/问题记录/exe-missing-tkinter.md) |
| 11 | `rm -rf dist && pyinstaller` 打包没执行 | safe-delete 短路 | [build-rm-blocked](docs/问题记录/build-rm-blocked.md) |
| 12 | DDC/CI 枚举不到显示器 | OSD 开关 · 直连 | [ddc-enumeration-empty](docs/问题记录/ddc-enumeration-empty.md) |
| 13 | 定时任务到点不生效（路径类） | task_target · 启动自愈 | [task-path-invalid](docs/问题记录/task-path-invalid.md) |
| 14 | CCD 不能直接断开主屏（rc=87） | PATH_ACTIVE · 主屏护栏 | [topology-primary-rc87](docs/问题记录/topology-primary-rc87.md) |

## 拓扑后端设计定稿（阶段 D，2026-11-27）

> 依据：`docs/拓扑探针.md` 的 A/C 阶段真机实测，不是推演。
> 目标：准确、稳定、跨机器通用；**默认路径 `ddc` 不变**。

### 实测确定的事实

| 事实 | 数字 / 结论 |
|---|---|
| `QDC_ALL_PATHS` 的 path 数 | 233 条 ≠ 屏数；只对应 3 块屏（2 物理 + 1 VDD） |
| `DISPLAYn` | 同一块屏对应 4 个 source（DISPLAY1/2/3/4）→ **不能当标识** |
| 稳定锚点 | 显卡 LUID + EDID mfr/prod + `connectorInstance`，两块同型号屏靠 1/2 区分 |
| VDD | `VDD by MTT`，inactive 但 `targetAvailable=1`，可独立识别 |
| 断开 / 接回 | 副屏 rc=0 / 805ms，接回 986ms；DDC 在 inactive 时读不到、接回后恢复 |
| 主屏断开 | rc=87 `ERROR_INVALID_PARAMETER`，Windows 不允许直接移除主屏 |
| 计划任务 | Interactive principal 下 CCD 查询 rc=0 → 定时触发可行 |
| 回滚 | C5 强制回读失败后，原始字节快照成功恢复，两屏均 ACTIVE |

### 决策

| # | 决策 | 依据 |
|---|---|---|
| D1 | 稳定锚点 = LUID + EDID mfr/prod + connectorInstance；EDID 无效退回 SetupAPI `DeviceID` 哈希 | 两块同型号屏唯一区分；`DISPLAYn` 不唯一 |
| D2 | 新增 `screen_off_method = topology` 作为**选项**，默认仍 `ddc`；提交带 `SDC_SAVE_TO_DATABASE`；已断开则 no-op | 不破坏已验收路径；断开语义跨重启保持 |
| D3 | 档位顺序：`off` 跳过亮度；`on` = 恢复快照 → 回读 → 有界轮询 ≤2s → 写亮度 | inactive 时 DDC 不可读；接回实测 <1s |
| D4 | VDD 永不作为目标；主屏直接拒绝；活动物理屏 ≤1 拒绝 | rc=87 实测；Sunshine 依赖 VDD |
| D5 | 旧 `monitors` / `monitor_ids` 只读兼容，新增 `monitor_anchors`，首次保存时迁移 | 不静默改变用户已验收的勾选 |
| D6 | 动作前存原始字节快照；回读失败自动回滚；最多 1 次回滚，错误码原样上报 | C5 实测有效；避免 sunshine 那种无限重试卡死 |

### 实现顺序

E1 只读层接入（`screen_verdict` / `--diag`）→ E2 身份层替换 `DISPLAYn` →
E3 动作层（快照 / 回读 / 回滚 / 护栏）→ E4 配置与 GUI → E5 串流回归（用户执行）。
每步独立可停；单测全部 monkeypatch，不碰真机屏幕。

**进度**：E1、E2 已完成（2026-11-27）。
- E1：主脚本内新增 CCD 只读层，`--diag` 可看到每块屏的 ACTIVE / 锚点 / 同源别名；
  默认熄灭方式的行为由 4 个单测守住。
- E2：`monitor_anchors` 优先解析，旧的 `monitor_ids` / `monitors` 依次兜底；
  真机闭环验证过"勾选 → 锚点 → 解析回同一组下标"。旧配置不写盘、继续可用。
- E3（动作层）未开始；跨机器待办仍未做，进 E3 前必须有结论。

## 待办：跨机器验证（用户指定，稍后执行）

当前所有实测只在**一台**机器（Win11 26100 / RTX 4070 SUPER + Intel 核显 /
Python 3.14 64bit / 双 R27qe Gen2 + `VDD by MTT`）上完成。进 E3 之前需在其它机器验证：

| # | 场景 | 要验什么 |
|---|---|---|
| 1 | 不同显卡厂商（AMD / Intel-only） | CCD `PATH_INFO` 布局、锚点是否仍唯一、`targetAvailable` 语义 |
| 2 | 无 VDD / 单屏机器 | 护栏是否正确拒绝；单屏拒绝断开 |
| 3 | Python 3.10 / 3.12、32 位 | `scripts/topology_probe.py` 开头打印的结构体尺寸是否与本机一致（`PATH_INFO=72` / `MODE_INFO=64` / `TARGET_DEVICE_NAME=420`） |
| 4 | 不同 Windows 版本 | 主屏 `rc=87`、`QDC_DATABASE_CURRENT` 行为是否一致 |

采集方式：在目标机器跑 `python scripts/topology_probe.py --json`，把 JSON 与结构体尺寸回传对比。
**这项不阻塞 E1/E2**，但进 E3（动作层）前必须有结论。

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。
