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

## 变更记录

见 [CHANGELOG.md](CHANGELOG.md)。
