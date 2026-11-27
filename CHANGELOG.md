# CHANGELOG

本文件记录项目所有值得注意的变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 2026-11-27 · v2.01.25：「记住」按钮加就地回执

用户反馈："记住显示器没有反馈，不知道是否成功。"

- **根因不是没写反馈**：`remember_layout` 一直有 `status_var.set(...)`，
  但**设置弹层是模态的、盖住主窗口的状态卡**，用户根本看不见。
- 修法：结果直接显示在按钮自己身上 —— 成功变「已记住 ✓」（1.8 秒后复原），
  快照失败变「快照失败」（2.6 秒）。弹层先被关掉时静默跳过，不抛异常。
- 单测 +1（`test_remember_btn_flashes_result`），169→170。

### 2026-11-27 · v2.01.24：第 4 轮走查（默认值变更影响）

默认熄灭方式改成 topology 属高风险改动，按 B6 变更影响分析走查 6 条受影
响链路（定时熄灭、CLI、主界面全选熄灭、逐台熄主屏、定时带亮度、首次检测）。

- **S-40/41 无人值守熄灭**：`topology_off(indices, cfg)` 不传 heir →
  `topology_plan_off` 走自动选择（优先物理屏，其次 VDD），不需要弹窗 ✅
- **S-42 全选熄灭**：有 VDD → 进入串流形态（只留 VDD）；无 VDD → 明确报
  "断开后一块屏都不剩"，拒绝黑屏 ✅
- **S-44 定时带亮度**：topology 下跳过亮度（inactive 屏读不到），点亮时在
  接回后写 ✅
- **P-31 🟡（已修）**：`_first_run_detect` 把 `monitors: []`（=全部）写成
  `[0,1]`，以后插新屏不会自动纳入。改为**只写 anchors**，不动
  `monitors`/`monitor_ids`——锚点只负责"认得住屏"，不承担"范围"语义。

**风险提示（已按官方文档/源码交叉验证，纠正此前口头说法）**：
远控会不会黑屏，取决于**它抓的那块输出还在不在线**，不是"物理/虚拟"之争。
- 抓「主显示器」或自带虚拟屏的（Sunshine 默认选默认显示器；ToDesk 官方说明
  虚拟屏开启后"原显示器会显示黑屏"、远程正常）→ 拓扑断开后主屏已交给在线
  虚拟屏，**画面正常**。
- 只有把输出名**写死**成被断开的物理屏（如 Sunshine `output_name = \\.\DISPLAY1`）
  才会失败。
- **Win 11 24H2 已知回归**：选中显示器离线/断电会让 DXGI 整体初始化失败
  （Sunshine issue #5673、24H2DummyFix）。所以"一定留一块在线屏"才是关键，
  默认拓扑反而确定性最高；DDC 待机在深度睡眠面板上也可能触发该问题。
- 依据：微软 DXGI `DuplicateOutput` 文档（per-output、模式变更使 duplication 失效）、
  Sunshine `output_name` 文档、ToDesk 帮助中心「虚拟屏」、24H2DummyFix。

### 2026-11-27 · v2.01.23：默认熄灭方式改为「拓扑断开」

用户："设置应该是默认拓扑断开的。"

- `SETTINGS` 里 `screen_off_method` 的 `default` 从 `ddc` 改为 `topology`。
  理由：这条路只改 Windows 拓扑、不碰面板电源，唤不回的风险最低；本机面板
  能力串不声明 `0xD6=2`，ddc 那条路本来也要"明知有风险"的授权才能走。
- 设置页 Segmented 的 fallback 从硬编码 `"system"` 改为跟 `SETTINGS` 默认值走。
- **不动** `load_config` 里"system 且未授权 ddc → ddc"的反向恢复：那是撤销
  v1.9.0 误迁移的历史逻辑，恢复的是用户原本的选择，与默认值无关。
- 测试：默认值断言改为 topology；两处依赖默认 ddc 的用例改为显式指定模式
  （测的是特定路径，不该依赖默认值）。

### 2026-11-27 · v2.01.22：第 3 轮走查（交互与刷新遗漏，4 项）

按 `scenario-walkthrough` 第 3 轮：回归重走 + 探索会话
（charter「弹层与托盘交互」）。本轮全是 🟡/⚪，无 🔴。

- **P-27 设置页滚轮失灵**：滚轮靠 Canvas 的 `<Enter>` 才 `bind_all`，鼠标从
  外部直接进到 Label/Frame 上时 Canvas 收不到 Enter（Tk 事件不冒泡）→
  只能拖滚动条。改为 `show()` 时 `bind_all`、`<Destroy>` 时解绑；
  无滚动条时不吞滚轮。
- **P-29 切换熄灭方式不重建列表**：`apply_off_method` 只 `probe_screens`，
  ddc→topology 后看不到可接回的物理屏。补 `refresh_monitors(silent=True)`。
- **P-30 托盘唤起不刷新显示器**：`_tray_show_main` 只 deiconify+lift，
  窗口在托盘期间拔插屏/开关串流后打开还是旧列表。补刷新 + 探测。
- **P-28 "Generic PnP Monitor" 被当名字**：CCD 查询失败时 desc 兜底成
  驱动占位名。`display_name_for_row` 过滤该占位名（VDD 分支同样处理）。

三轮总计：27 个问题、8 个 🔴 全修、单测 150→169、坑库六类根因。
停止理由：达到技能"轮次上限 3 轮"，残余风险见 `docs/walkthrough/report.md`。

### 2026-11-27 · v2.01.21：名称秒显真名 + VDD 始终列出 + 打包目录固定

用户截图（20:05）：显示器只剩"显示器 0/1"、VDD 不见、状态栏提示旧配置。
走查后确认这三个现象同一个根因：**新目录没有 config.json**。

- **根因（实测，非推断）**：`monitor_model()` 走 DDC 读 EDID，两块屏
  **6213ms**；旧配置里有 `models` 缓存所以秒显型号，配置一重置就只能
  先显示"显示器 0"。VDD 则是 `refresh_monitors` 只在 topology 模式才探测
  断开屏，默认 ddc 模式下整条列表为空。
- **名称秒显**：`refresh_monitors` 改为**任何模式**都调 `ccd_probe_topology()`
  （实测 0.5ms），填 `_ccd_names`；`display_name_for_row` 在型号缓存未到位时
  用 CCD friendly（"R27qe Gen2"）兜底，不再显示"显示器 N"。
- **VDD 始终列出**：ddc/system 模式也把虚拟显示器列进 `topo_inactive`
  （灰显、无按钮、副标题说明"由串流软件管理"）。物理断开屏仍只在
  topology 模式列（只有该模式能接回）。
- **打包目录固定为 `dist`**：不再每次建 `dist_v201xx`。配置便携化后，
  换版本目录就等于配置丢失（用户每版都要手动拷）。现在覆盖 exe、配置与
  日志原地保留，双击同一路径即最新版。历史版本请先整目录复制再打包。
- 已把 `%APPDATA%` 的旧配置与布局快照复制到 `dist/`（源文件保留不删）。

### 2026-11-27 · v2.01.20：走查待决策项处理（不自动迁移 + 恢复并发护栏）

用户决策："② 不自动迁移，这个迁不迁移的也不重要，每个电脑都不同。"

- **P-21 改为不自动迁移**：删掉 `_migrate_old_config()` 与 main 里的调用；
  `save_config` 也不镜像回 APPDATA（配置只有程序目录这一份）。
  新增 `old_config_present()`：程序目录没有配置而 APPDATA 有时，启动后
  状态栏提示一句（含路径），由用户自己决定要不要拷，并记入 events.log。
- **P-22 恢复双屏并发护栏**：倒计时到点时若 `_busy`（用户刚点了熄灭/点亮），
  直接取消并提示"有屏幕操作正在进行"，避免两个任务同时改拓扑、
  `_busy` 归属错乱（先完成的先把按钮启用了，另一个还在跑）。
- **P-24 单测隔离**（走查时自查发现）：`TempConfigCase` 与 `setUpModule`
  都把 `_LEGACY_CONFIG_DIR` 一起指到临时目录 —— 否则任何碰到
  `save_config` 的用例都会写进用户**真实的** APPDATA 配置。
- 单测：删除迁移用例，新增"保存不碰旧目录""旧配置只报告不复制"两项，166→167。

### 2026-11-27 · v2.01.19：按 scenario-walkthrough 走查修复 4 项

用户："使用仓库里的抽查技能，对程序走查吧，又是反复的小错误。"
按 `workbuddy-skills/skills/scenario-walkthrough` v3 做变更影响分析
（v2.01.11→工作区 +687 行、24 个新函数），走查 9 条新链路，发现 6 个问题。

**已修 4 项**：
- **P-18 🔴 VDD 名称修复无效**：v2.01.12 用 DDC 的 `desc` 取名，真机实测
  `desc` **恒为 "Generic PnP Monitor"**。改 `App.ccd_friendly()` 优先取 CCD 的
  `monitorFriendlyDeviceName`（"VDD by MTT"），desc 仅兜底。
- **P-23 🟡 首次检测把单选改成全选**：`_first_run_detect` 无条件全选，老配置
  缺锚点时用户的选择被静默改掉。改为先 `resolve_monitors(cfg)`，[] 才全选。
- **P-20 🟡 Tooltip 覆盖按钮 hover**：`bind` 不带 `add="+"` 替换了自绘 Button
  的 `<Enter>`，按钮不再高亮。四个事件全部追加绑定。
- **P-19 🟡 恢复双屏误报失败**：快照含 VDD 时 `allow_vdd=False` 把目标过滤掉，
  永远等不到确认。验证前按当前拓扑剔掉 VDD 锚点。

**待决策 2 项**（见 `docs/walkthrough/report.md` 第 2 轮）：
- P-21 便携化后换 `dist_*` 目录，配置从 APPDATA 旧快照迁移 → 回退。
- P-22 倒计时到点与屏幕动作理论上可并发（低概率）。

坑库新增第 5 类根因「修了名字却没修名字的来源」：显示/提示/默认值类改动
必须打印真实取值验证，不能只读代码。单测 164 → 166。

### 2026-11-27 · v2.01.18：修交接弹窗候选少一个（截图实证）

用户 19:46 截图：3 块屏（主屏 Legion R27qe Gen2 active + 物理屏 R27qe Gen2
断开 + VDD 断开），点主屏「熄灭」的交接弹窗里**只有 VDD 一个候选**，应该 2 个。

**根因（纯函数精确复现，非猜）**：
`selection_anchors([主屏])` 按 `gdi` 匹配锚点，但没排除 inactive 行。物理屏断开后
没有 ACTIVE path，折叠后残留的 gdi 和主屏撞名（都是 DISPLAY1），于是主屏和断开屏
**都被命中** → `topology_heir_candidates` 的 skip 把两块都排除 → 候选只剩 VDD。
复现脚本输出：命中 `['A1','A2']`、候选 `['VDD']`，与截图完全一致。

**修法（依据微软官方文档 DISPLAYCONFIG_PATH_ACTIVE = "路径处于活动状态且是
桌面的一部分"）**：`selection_anchors` 匹配时加 `if not r.get("active"): continue`，
只认 ACTIVE path 的 gdi。修复后：命中 `['A1']`、候选 `['A2','VDD']` 两个。

单测 +1（`test_inactive_screen_with_same_gdi_does_not_steal_anchor`），163→164。

### 2026-11-27 · v2.01.17：配置便携化 + 首次启动自动检测

用户："配置文件的路径是程序所在文件夹吗""第一次打开需要自动刷新显示器"。
两项都要（用户明确选"改到程序文件夹"和"两个都要"）。

**配置便携化**（检索依据 SO #4897525：便携版配置放程序根目录是标准做法）：
- `CONFIG_DIR` 改为程序目录（可写时），不可写（Program Files/只读介质）才
  回退 `%APPDATA%` —— 和日志同策略。现在 `config.json`、`default_layout.json`、
  三个日志都在程序同目录，整个目录拷走就能用。
- 旧配置迁移：首次运行把 `%APPDATA%\MonitorBrightness` 下的 `config.json` 和
  `default_layout.json` **复制**到程序目录（SO #37822688 的一次性 Upgrade 标志
  模式：只在目标不存在时复制、不覆盖、旧的保留留后路）。仅 `main()` 调用，
  单测无副作用。
- `.gitignore` 新增 `config.json` / `default_layout.json` — 用户配置绝不入库。

**首次启动自动检测**（检索依据 SO #20577621 / #49682006：`after_idle` 是
Tk 官方推荐的"窗口显示后再初始化"做法，Bryan Oakley 也推荐）：
- 启动时若配置里 `monitor_anchors` 为空（首次打开），用 `after_idle` 补一次
  全量 `probe_screens()`，并把当前检测到的显示器锚点/index/设备名写进配置。
- 之前锚点只在用户手动勾选时才存，首次打开配置里 anchors 是空的，定时/熄灭
  逻辑只能靠纯 index 兜底，换屏或重排后会认错屏。

单测 +1（`test_migrate_old_config_copies_without_overwrite`），162 → 163。

### 2026-11-27 · v2.01.16：修复悬停气泡"有的显示、有的不显示"

用户报："悬停没有正确显示""有的有显示 有的没有"。按要求先检索交叉验证
（StackOverflow #12356068 Bryan Oakley：**事件不向父控件传播**；
#39551850：绑在 Frame 上的 Enter/Leave 不触发、绑子控件才行；
#9135302 / #69869659：模态/置顶父窗口下 tooltip 要加 `-topmost`），
再按可信结论修，不靠猜。

- **根因 1**：v2.01.15 把 `<Enter>` 绑在 `row` 这个 Frame 上，但 Tk 事件
  **不冒泡**——鼠标停在里面的 Label/Switch 上时父 Frame 收不到。修法：
  `Tooltip.bind_tree()` 递归给整棵子控件树绑定；`Sheet.row` 在 builder
  之后调用（此时子控件才建好）。
- **根因 2**：设置弹层是 `grab_set()` 模态窗口，气泡 Toplevel 没设
  `-topmost`，被模态窗口按 Z 序压住——这就是"有的能看到有的看不到"。
  修法：气泡加 `attributes("-topmost", True)`。
- 单测：新增 `test_tooltip_on_child_widget`（断言 bind_tree 覆盖子树，
  不用 event_generate + 固定 update 轮数，避免 CI flake）。159→162。

### 2026-11-27 · v2.01.15：设置说明改成悬停气泡

用户："设置里面太多说明文字了，修改成鼠标移动到菜单，弹出说明显示。"

- 新增 `Tooltip` 类：鼠标停在控件上 400ms 后弹深色气泡，跟随鼠标、超出屏幕
  自动翻边；Enter/Leave/按下/失焦都会正确显示或销毁，不留残影。
- `Sheet.row` 新增 `tip` 参数：给了 tip 就不显示 `sub` 文字，说明全部藏进
  气泡；不传 tip 的调用（如 HeirSheet 的候选说明）行为完全不变。
- 设置页 9 处说明 + "熄灭方式"三种方式的解释 + "三个快捷按钮的值" 全部
  改成悬停气泡。
- 正文高度 **869px → 636px**（再降 27%）；滚动条封顶同步 650 → 520px，
  保证滚动条仍明确出现（用户要"有滑动条"）。
- 单测 +2（`TooltipTest`）：气泡显示/隐藏/文字正确、row 传 tip 藏 sub。
  150 → 161。

### 2026-11-27 · v2.01.14：设置页紧凑化 + 滚动条封顶 650px

用户："设置页重构一下，太长了，而且空间占用太多了。也没有滑动条。"
（后者是因为他还在跑 v2.01.12；但这轮把"太长/太松"也一并解决了。）

**紧凑化**（只改布局，不删任何功能）：
- `Sheet.row` 的 `pady` 9 → 4；副标题 `wraplength` 加到 `width-130`，少换行。
- `Sheet.section` 的标题 pady (14,6) → (9,3)（5 个分区省 ~90px）。
- 删掉设置页"熄灭方式"下 4 行说明（和「说明」页完全重复），改成一句
  "各方式区别见「说明」"（省 ~70px）。
- "关于"区 5 行 → 3 行：版本与配置路径合并；诊断开关和「日志/导出」
  两个按钮并排一行。
- 窗口宽度 440 → 480。

实测正文高度 **1232px → 869px**（降 29%）。

**滚动条**：用户要"要有滑动条"，封顶从 `screenheight-220`（本机 1220px）
改成 **650px**（下限 400）。现在设置正文 869px > 650px，滚动条明确出现、
窗口更矮；交接弹窗内容小（237px）不受影响，仍无滚动条。

### 2026-11-27 · v2.01.13：设置弹层滚动条 + 移除全局热键 + 修行变量覆盖

- **设置菜单滚动条**：`Sheet` 正文原来只是裸 Frame（文档却写着"带滚动正文"），
  设置项一多底部就点不到。现在所有弹层正文统一包一层 Canvas + 垂直滚动条，
  高度按屏幕封顶（`screenheight - 220`），超出才出滚动条、支持鼠标滚轮。
  实测设置正文 1232px > 封顶 1220px，滚动条正确出现。
- **移除全局热键**（用户要求"取消不要了"）：删掉设置项、启动监听、
  `RegisterHotKey` 线程、托盘/退出清理、配置 key；自动复亮的队列信号从
  `hotkey` 改名为通用的 `screen_action`（功能不变）。旧配置里残留的
  `screen_hotkeys` 字段会被 `load_config` 忽略，不报错、不迁移。
- **修行变量覆盖**：`MonitorPanel.rebuild` 里我把局部变量 `row`（那一行
  tk.Frame）覆盖成名称字典，导致 `self.mon_rows[i]` 存了错东西。虽然目前
  `mon_rows` 只写不读、没造成可见故障，但属于埋雷，改名 `name_row` 修掉。

> **交接弹窗候选数（用户报"3 块屏、出错那次只显示 1 个"）**：本轮未能复现 ——
> 真机只读探针显示三块屏都 connected，`topology_heir_candidates([主屏锚点])`
> 实返 2 个候选（正确值），`HeirSheet` 渲染正常。日志 18:38 的三次交接都成功。
> 按仓库规则"只修被复现的 bug"，**没改候选过滤逻辑**。改了两处零风险的辅助：
>  1. `_ask_heir` 加 `dlog("app.heir_prompt", ...)`：记录候选数与每块的
>     anchor/vdd/active，下次出错看 diag.log 就知道是不是当时
>     `targetAvailable=False`（串流软件停了）还是查询失败；
>  2. HeirSheet 给物理屏候选加序号（"物理显示器 1/2"），两块同型号屏
>     friendly 都叫 "R27qe Gen2" 时不再分不清。

### 2026-11-27 · v2.01.12：「恢复双屏」按钮（倒计时可配）+ 修 VDD 当主屏时名称错位

**起因**：用户把物理屏全断开串流后，屏幕无法恢复，要求加一个"点一下、倒计时后
自动恢复默认双屏"的按钮；同时指出虚拟显示器当主屏时名字显示错。

**恢复双屏**

- 状态卡新增「恢复双屏」按钮。点击 → 先做 `SDC_VALIDATE` 预检（不碰桌面）→
  倒计时（默认 10 秒，设置 → 熄屏 → 「恢复双屏倒计时」可改 0~120）→ 到点整份
  CCD 快照写回。倒计时期间按钮显示剩余秒数、右侧出现「取消」；最小化到托盘会
  自动取消，不在用户看不见时偷偷恢复。
- 默认布局快照与 `monitor_positions` 一起记：**只在用户主动点「记住布局」时写**
  （`%APPDATA%\MonitorBrightness\default_layout.json`），不自动覆盖。理由同
  v2.01.11：交接主屏那一刻的布局不是用户摆的。
- 为什么整份写回而不是逐个 `topology_on`：VDD 占着 (0,0) 时逐个接回会把物理屏
  挤到桌面最右（17:40 的 diag.log 实证 `saved_positions` 全 (0,0)、
  `layout=[[0,0,1920,1080]]`）。整份快照同时做到接回两屏 + VDD 退场 + 摆回原位。
- 校验旗标坑：`SDC_VALIDATE` 必须与 `_CCD_APPLY_FLAGS` 完全同一套，只把 APPLY
  换成 VALIDATE；漏掉 `SAVE_TO_DATABASE` 会 rc=87 假失败（真机验证抓到并修）。
- 快照长度不符 / 校验被拒 → 一个 `SetDisplayConfig` 都不发，直接报错让用户
  重新「记住布局」。

**VDD 名称错位**

- 证据：配置 `models={"0":"Legion R27qe Gen2", ...}` 按 index 存；
  diag.log 18:38:43 `topo.off.handover heir=1589A778.../VDD`（VDD 接班成主屏）
  后它成为 index 0，界面就套用了物理屏的型号名。
- 修复：抽纯函数 `display_name_for_row()` —— VDD 行永远用自身 friendly 名
  （"VDD by MTT"）或"虚拟显示器"，不读 `mon_models[index]`；
  `_fetch_models` 也不再给 VDD 缓存型号，防止旧配置残留串味。
- 单测：`VddNameTest` 4 项、`DefaultLayoutTest` 5 项。合计 150 → 159。

> 注意：新用户第一次用要点一次「记住布局」才会生成默认布局快照，
> 「恢复双屏」在此之前会提示"还没记住默认布局"。

### 2026-11-27 · v2.01.11：位置记录不再被交接污染 + 「记住布局」 + 虚拟屏不给按钮

用户："你看看日志，现在出 bug 了。"

**日志还原（17:36~17:38）**

```
17:36:36 断开主屏 A2，交接给 A1（当时 A1 是断开的）
         → 记录 A2 = (0,0)        ← A2 被交接挪到原点，这是"被迫的位置"
17:36:43 断开新主屏 A1，交接给虚拟屏 VDD
         → 记录 A1 = (0,0)        ← 同样
17:38:33 接回 A1 → 记录 (0,0)，但 VDD 占着 (0,0)，退到 1920
17:38:39 接回 A2 → 记录 (0,0)，也退到 4480
         最终：VDD(0,0) + A1(1920) + A2(4480)，三块屏并排挂着
```

根因：**位置记录会被交接污染**。交接主屏必然把接班屏挪到桌面原点，
那一刻的"真实位置"不是用户摆的；照它记下来，下一次接回就再也回不去了。

**改动**

1. 位置记录**不再自动覆盖**：断开时只在"没有记录"时补一条，已有的不动。
   用户想更新布局，用新入口 **设置 → 熄屏 → 「记住当前显示器布局」**
   （`remember_layout_snapshot()`，主线程同步做，一次 CCD 查询 ~50ms）。
   这同时回答了 v2.01.9 留下的"方案 1 / 方案 2"——两个都不要，
   改成"默认不覆盖 + 主动记住"，最不偷偷动用户的东西。
2. **虚拟屏不给逐台按钮**：VDD 当主屏时它也会出现在列表里，之前给了
   「熄灭」，点了只会"拿不到锚点"（VDD 永不作目标），纯属骗人。
   `App.is_vdd(index)` + `refresh_monitors` 顺手记下虚拟屏设备名。
3. 真机手工把三块屏收拾回"A1 (0,0) + A2 (-2560,0)、VDD 摘掉"
   （`SetDisplayConfig rc=0`，位置记录对齐）。

测试 148 → 150。

### 2026-11-27 · v2.01.10：启动默认全选所有显示器

用户："打开的时候，应该默认勾选两个显示器，这样我可以方便的，一起调节亮度。"

之前启动会沿用 `cfg["monitors"]`（上一轮只勾了一块），每次开程序都得先手动补勾。
现在 `_restore_selection(prev)`：

- `prev` 为空（启动）→ **全选**；
- `prev` 非空（刷新：改分辨率、插拔）→ 保留用户当前勾选，不打断他正在做的事；
- 越界的旧下标丢掉。

定时任务的目标仍是另一套解析（`resolve_monitors` 读 `monitor_anchors`），
不受界面勾选影响。

> 另外：用 `scripts` 外的一次性脚本把真机布局修回了"副屏在左"
> （A1 `(0,0)` + A2 `(-2560,0)`，`rc=0`，窗口跟着搬），并把
> `cfg["monitor_positions"]` 对齐成这个真实布局，便于下一轮测试。

测试 145 → 148。

### 2026-11-27 · v2.01.9：改显示器时，应用窗口跟着自己的屏走

用户："还是那个问题，窗口的位置关系变化了。它能记录窗口位置关系吗，这样修改的
时候，可以正确的恢复。"

交接主屏会让所有屏平移（接班屏挪到桌面原点），Windows 这时会把各应用的窗口
挤到"还能显示"的地方，而且**拓扑恢复后不会搬回来**——用户看到的就是"一改设置，
窗口全乱"。

**做法：动作前记，动作后搬，只在这一次操作内**

- `win_snapshot(layout)`：枚举应用窗口，按**窗口中心点**落在哪块屏的矩形里，
  记下 `{hwnd, rect, anchor}`。
  过滤掉：不可见、最小化（动它等于还原，是另一回事）、无标题、工具窗/不激活窗
  （输入法、悬浮条）、本进程自己的窗口。
- `win_follow(snap, shift, dropped)`：按屏的平移量把窗口挪过去；
  **所在屏被摘掉的窗口不动**（那块屏没了，交给 Windows 挤到剩下的屏上）；
  只在窗口确实偏离目标位置时才动手，已经在位的不碰。
- 只在 `shift ≠ 0`（真的会平移，也就是涉及主屏交接）时才做。普通断开副屏
  shift 为 0，窗口不用动。
- 开关：设置 → 熄屏 → 「改显示器时窗口跟着屏走」（默认开）。
- 不落盘、不跨操作：跨操作记录会跟用户后来的手动摆放打架，他挪过的窗口
  会被我们又搬回去。

真机只读验证：可搬顶层窗口 14 个，13 个正确映射到所在屏（`68C7/1` 在 0，
`68C7/2` 在 2560）。

测试 139 → 145。

### 2026-11-27 · v2.01.8：断开主屏前先交接主屏角色（纯拓扑，不碰面板电源）

用户提出的方案，比我上一版好：

> 可以修改为，禁用主显示器，会先切换主显示器，然后再禁用吗，这样不就没问题了。
> 弹窗就会让选择使用哪个显示器是主显示器。如果禁用所有显示器，就是把虚拟显示器
> 设置为主显示器。

上一版我把主屏的「熄灭」引到 DDC 待机——那是唯一有"唤不回"风险的路。用户这句
话绕开了它：**先交接主屏角色，再纯拓扑断开**，全程不碰面板电源。

**关键技术事实**：Windows 没有"设置主显示器"的 API。主屏就是桌面原点 `(0,0)`
所在的那块屏，所以"交接" = **把接班屏挪到 `(0,0)`**，其余屏跟着平移。
`scripts/topology_handover_probe.py` 只读验证（SDC_VALIDATE）：

| 做法 | rc |
|---|---|
| 只改 source mode 的 position | 0 |
| 接班屏移到 (0,0) + 摘掉原主屏 | 0 |
| **VDD 当主屏 + 物理屏全摘** | **0** ← 串流形态 |

**改动**

- `topology_plan_off()` 重写：允许把主屏放进"要摘掉"的名单，但要求有人接班，
  返回 `{off: [...], heir: 锚点, heir_active: bool}`。护栏从"活动物理屏 ≤1 拒绝"
  换成"断开后桌面必须还剩至少一块屏"（否则黑屏，Windows 也不允许）。
  接班屏优先级：先物理屏（用户看得见），全部物理屏都要断时才是虚拟屏。
- `topology_off(indices, cfg, heir=...)`：一次提交里完成「接班屏挪到 (0,0) +
  其余屏平移 + 原主屏摘掉」；接班屏若是虚拟屏，先按 v2.01.2 的合成逻辑接回再放 `(0,0)`。
  位置记录按**平移后**的坐标存，接回时正好落在新主屏旁边。
- 新增 `HeirSheet`：点主屏那行的「熄灭」时弹窗「把主屏交给谁」，列出可接任的屏。
  没有候选就直说"断开后一块屏都不剩（会黑屏）"，不硬提交。
- 死代码：上一版的 DDC 待机分支删掉（那条路只有风险，没有必要性）。

**顺带修掉一个真 bug（日志里发现的）**

16:49 的日志：点**单台**的「显示」，`screen.on.enter` 却带着**两块屏**的锚点。

根因在 `selection_anchors`：它拿 `gdi + aliases` 一起匹配，而
`QDC_ALL_PATHS` 下**每块屏的 aliases 都列出 DISPLAY1~4 全部四个名字**
（同一 target 的所有 source），所以任何勾选都会命中所有屏。
后果不只是"点一块亮两块"，`_remember_selection` 还会把两块的锚点都写进
`monitor_anchors`，让定时任务对多块屏生效。

改成只按 `gdi`（当前 ACTIVE 的 source 名）匹配。实测：
`selection_anchors([0]) → [.../68C7/1]`，`[1] → [.../68C7/2]`。

测试 133 → 139。

### 2026-11-27 · v2.01.7：行内按钮按状态切换，主屏「熄灭」改成问一句

用户："灰色的熄灭是正常的吗，感觉不符合直觉呢。"

确实不符合。灰按钮同时说了两句矛盾的话——"这里有个功能"和"你用不了"。
而且主屏明明是亮的，旁边还摆着一个「显示」，更奇诡。

**改法（用户选的方案）**

1. **一行只有一个动作按钮**：亮着给「熄灭」，灭着给「显示」。
   按钮只表达"现在能做的那一件事"，不再有灰按钮。
   - 状态看**上一次探测**的逐台结论（`_screen_states`），不按模式猜；
   - 探测不到（unknown）就按"亮着"给「熄灭」；
   - 拓扑模式下主屏被 DDC 待机后，探测会报 `off`，按钮自动变「显示」——
     否则用户就唤不回来了。

2. **主屏的「熄灭」可点**：点了先解释"拓扑模式移不走主屏（Windows 实测 rc=87）"，
   再问一句要不要改用「单屏待机」把面板熄灭，并写明风险（个别面板唤不回）。
   - 同意 → 走 `screen_off_many([idx], allow_undeclared=True)`；
   - **这次授权只管当下这一下**，不写进配置（pitfalls N-04：一次性授权不能变永久）。
   - 不同意 → 什么都不做。

3. 新增 `screen_on_row()`：拓扑模式下点「显示」时，先看有没有真的需要接回 ——
   接回了就写亮度；本来就在拓扑里（`restored` 为空）说明只是面板被待机了，
   改走 DDC 唤醒，且**禁止切信号源**（拓扑模式下切源会把副屏切到空口）。

测试 132 → 133。

### 2026-11-27 · v2.01.6：界面不再挂历史事件（那几条不是报错）

用户截图问："一直显示这些 是有问题吗"。

那三条是 16:29 / 16:39 的历史记录：

```
拓扑接回：…/AE30/68C7/2 的 source mode 已不在 modes 数组里（断开后被 Windows 摘掉），
现造一条 2560x1440 放在 (-2560,0) 补在第 5 位
```

它记录的是**一次成功接回**的实现细节，不是错误。挂在界面上有两个毛病：

1. **读的是整个 events.log 的历史** —— 开机就把上周的排障记录摆出来，
   永远不会自己消失（直到被更新的 3 条挤掉），看着就像"一直在报错"。
2. **说的是内部术语** —— "source mode""modes 数组""补在第 5 位"，
   对用户没有信息量，只会增加焦虑。

**改动**

- `read_session_events()`：界面只读**本次运行**写入的事件（内存里那份），
  不再读 `events.log` 文件。历史仍留在文件里，给 `--diag` / 诊断包用。
- `_ccd_fill_missing_source_modes` 里那条 `_write_event_log` 删掉 ——
  `_write_event_log` 的既定规则就是「不得在熄灭、点亮调用链里使用」，
  我在 v2.01.2 加它时违反了自己写的规则。细节改走 `dlog`。
- 降级重试那条改成说人话：
  「接回显示器：第一套参数被 Windows 拒绝（rc=87），已自动换一种方式重试」
- 没有事件时把那一行 `pack_forget`，不留空白条。

> 顺带说明：截图里的「任务计划：未注册」不是故障 —— 没打开「启用每日定时」
> 就是这个状态，打开开关就会注册。

测试 130 → 132。

### 2026-11-27 · v2.01.5：日志改到**程序同目录** + 一键导出诊断包

用户原话："你这个日志明显应该跟程序同目录，我才好发给你呢。"

**1) 日志目录：程序目录优先，不可写才回退 APPDATA**

- `LOG_DIR = _pick_log_dir()`：打包态取 exe 所在目录，源码运行取脚本目录。
- 可写性**真写一个探针文件再删**来判断 —— `os.access(W_OK)` 在 Program Files
  下会骗人（返回 True，写的时候才报错）。
- 装在 Program Files / 只读介质 / 网络盘上时，自动回退 `%APPDATA%\MonitorBrightness`，
  `--diag` 里会写明当前用的是哪一种。
- 老版本的 `events.log` / `diag.log` 在 `main()` 里**搬一次**过来（不覆盖已有），
  用户之前那些记录不会丢。搬移只在 `main()` 里做，单元测试没有这个副作用。
- 配置本身仍留在 APPDATA：换版本目录（dist_v2014 → dist_v2015）时设置不丢。

**2) 「导出诊断包」**

设置 → 关于 → 「导出」：把 `diag.log` / `events.log` / `error.log`（含轮转的 .1）、
`config.json` 和一份 `环境.txt`（版本、目录、熄灭方式、锚点、位置记录）打成一个
zip，放在程序目录，并在资源管理器里选中。命令行也有：`--diag-export`。

**3) 修「可以熄灭的显示器没有正常显示」**

拓扑模式下我把主屏那行的「熄灭」**藏掉**了（因为 Windows 不让移除主屏）。
结果两行不一致，用户看到的是"这一行没正常显示"而不是"这里不可用"。

现在改成：**两种按钮始终都在**，不可用时置灰，并在副标题写明原因
（`… · 主显示器 · DISPLAY1 · 拓扑模式不可断开`）。

> 这是本轮第二次因为"同一件事在多处各写一遍"而出问题（上一次是日志路径）。
> 重构提案见对话，不在这里混着做。

测试 126 → 130。

### 2026-11-27 · v2.01.4：加一个**临时**诊断日志模块（问题收敛后整体删掉）

用户的诉求很直接："方便快速迭代定位问题，等软件没问题之后再把模块去掉。"
所以这一版的重点不是日志功能多强，而是**好删**。

**设计：只有两个函数，埋点全是单行**

- `dlog(tag, **fields)` —— 唯一入口，JSON Lines 落 `%APPDATA%\MonitorBrightness\diag.log`。
- `diag_dump(limit)` —— 给 CLI / 界面读最近若干行。
- 埋点一律写成单行 `dlog("topo.on.plan", ...)`，不写多行调用，
  将来 `grep -n "dlog(" monitor_brightness.py` 就能一次找全。
- 开关是配置项 `diag_log`（默认开），关掉后 `dlog` 立即 return，零 IO。

**删干净的四步**（写在代码里那段注释的开头）
1. 删掉 `DIAG_FILE` 那一段（含 `_DIAG_LOCK/_DIAG_ON/_DIAG_LIMIT`、`_diag_file`、
   `diag_set_enabled`、`diag_enabled`、`dlog`、`diag_dump`）；
2. 删掉 `SETTINGS` 里的 `diag_log` 一项，以及 `load_config` / `apply_setting`
   里那两行同步；
3. 删掉设置页「关于」里的两个 row 和 `App.open_log_dir`；
4. `grep -n "dlog(" monitor_brightness.py` 把埋点行整行删掉，外加 `--diag-log`
   分支和 `--diag` 里那两行输出。

**记什么（按前缀筛）**

| tag | 内容 |
|---|---|
| `app.start` | 版本、argv、是否打包态 |
| `app.action` | 点了哪个按钮、哪个下标 / 锚点 |
| `screen.*.enter/exit` | 熄灭/点亮的入口出口，含方式与结果 |
| `topo.off` / `topo.off.plan` / `topo.off.positions` / `topo.off.done` | 断开全过程 |
| `topo.on` / `topo.on.plan` / `topo.on.synth.*` / `topo.on.done` | 接回全过程（含合成 source mode） |
| `ccd.apply` | 每一次 `SetDisplayConfig` 的 pc/mc/flags/rc |
| `ccd.plan.write` | 就地改写了哪条 path、三个 modeInfoIdx 是什么 |
| `sch.decide` / `sch.result` | 定时档位判定的输入与结果 |

一次接回的完整链路可以直接串起来看：

```
findstr /c:"\"tag\": \"topo.on" diag.log
```

**用起来**

- 界面：设置 → 关于 → 「诊断日志」开关 + 「打开日志目录」按钮。
- 命令行：`MonitorBrightness.exe --diag-log` 直接打印最近 200 行；
  `--diag` 里也会显示日志路径和开关状态。

**顺带修掉的测试污染**：有些用例类直接继承 `unittest.TestCase`（不经过
`TempConfigCase`），跑测试会把假数据写进**真实的** `%APPDATA%` 日志目录 ——
实测把单元测试里的 `anchor=A2` 写进了用户的 diag.log。加了 `setUpModule`，
整份测试统一兜到临时目录。

测试 120 → 126。

### 2026-11-27 · v2.01.3：接回摆回原位 + 每行独立的「熄灭 / 显示」

**1) 接回后位置跳变（用户报的 bug）**

原因是我上一版偷懒：断开后 Windows 会把该屏的 mode 项摘掉，位置无从得知，
于是统一把接回的屏放到**桌面最右边**。用户原来把副屏放在左边，接回后跑到右边。

修法：**断开前把位置抓一次**（`monitor_positions`），接回时摆回去。
这不算"状态账"，界线在于：

| | 状态账（被用户否掉的） | 位置偏好（本次新增的） |
|---|---|---|
| 内容 | "屏幕现在是灭的" | "这块屏摆在 (-2560,0)" |
| 会不会和现实脱节 | 会（用户随时能改） | 不会 —— **每次断开前都按当下真实值重写** |
| 断开之后还读得到吗 | 读得到，所以没必要存 | 读不到（Windows 摘掉了 mode 项），只能事先抓 |

再退一步：位置与现有屏**重叠**时丢弃记录、退回最右边 —— 实测重叠会让
`SetDisplayConfig` 直接 rc=87。

真机验证（`scripts/topology_simulate_reconnect.py`，SDC_VALIDATE 只读）：

```
记录到的位置：{'…/AE30/68C7/2': [-2560, 0, 2560, 1440]}
不带位置：合成 pos=(2560,0)   ← 这就是"跑到右边"
带位置：  合成 pos=(-2560,0)  ✅ 与原位置一致，校验 rc=0
```

**2) 每行独立的「熄灭 / 显示」**

- 活动屏行右侧加「熄灭」「显示」；断开屏行的「启用」改叫「显示」
  （用户的原话是"熄灭和显示的按钮"）。
- 逐台动作**不改勾选**：点的是这一行，不是改选择（`_row_action`）。
- 系统级熄灭绝不给逐台按钮 —— 它是整组广播，给了按钮只会点了没反应。
- 拓扑模式下主屏行不给「熄灭」（Windows 不允许移除主屏，实测 rc=87），
  只留「显示」。
- `set_row_text()` 改成记住文本容器，不再按 `winfo_children()[1]` 猜 ——
  行里现在有「勾选 / 按钮 / 文本」三段，按下标猜必错。

### 2026-11-27 · v2.01.2：接回时现造 source mode（真机闭环，不再需要人肉试）

v2.01.1 上线后，真机点「点亮屏幕」弹的是：
> 锚点 00010CBD:…/AE30/68C7/2 的 source mode 不在当前 modes 数组里

这句提示本身是对的，也说明 v2.01.1 的判断没错——但**结论下错了**：
我把"source mode 不存在"当成"接不回来"，其实它只是"要去造一条"。

**关键工具：`SDC_VALIDATE` + VDD 样本**

一台机器上总有"连着、但整块不在拓扑里"的屏（本机是 `VDD by MTT`），
它的状态和"物理屏刚被断开"完全一样：path inactive、source mode 已从
modes 数组里被摘掉。拿它当样本，用 `SDC_VALIDATE` 反复试提交，
既不用真断物理屏，也不用请用户来点按钮。

`scripts/topology_mode_synthesis.py` 的真机矩阵：

| 做法 | rc |
|---|---|
| `sourceInfo.modeInfoIdx = INVALID` | 87 |
| 借用另一块屏的 source mode 项 | 87 |
| **自己往 modes 末尾补一条 SOURCE mode** | **0** |
| 自己补一条，但 `pixelFormat=32BPP` | 87 |
| 自己补一条，但位置 (0,0) 与主屏重叠 | 87 |

于是结论是：**自己补，pixelFormat 用 NONGDI(4)，位置不能和现有屏重叠。**

**顺手挖出一个会写坏堆的老 bug**

为了端到端验证，我写了 `scripts/topology_live_synthesis_check.py`：
拿 VDD 当样本真跑一次接回，`finally` 里无条件还原。第一次跑就段错误
（exit 139），faulthandler 指到一行无辜的 `print` —— 是堆被踩了。

根因在 `_ccd_make_snapshot`：
- `GetDisplayConfigBufferSizes` 给的是**缓冲上限**（本机 modes 699 条）；
- `QueryDisplayConfig` 只填真正用到的（本机 6 条）并把计数改回 6；
- 快照却用 `bytes(modes)` 存了整个 **699** 条（44736 字节），`modeCount` 仍记 6；
- 回滚时按 6 条建 384 字节的数组，再 `memmove` 44736 字节 → 越界写坏堆。

这是 `topology_off` 回滚路径上的既有 bug，只在"回读失败要回滚"时才触发，
所以一直没暴露。现在快照只取前 `count` 个元素，且长度不符时**拒绝提交**。

**改动**

- `_ccd_append_source_mode()`：在 modes 数组末尾补一条 SOURCE mode 项
  （NONGDI + 指定位置），不动原数组。
- `_ccd_fill_missing_source_modes()`：把 plan 里 source mode 已缺失的项补齐，
  尺寸取 `GET_TARGET_PREFERRED_MODE`（真机确认 type=3 才对），
  失败则抄一块正在用的屏，再不行才 1920x1080；
  位置取桌面最右边，多块时依次往右排。
- `_ccd_free_origin()` / `_ccd_live_size()` / `_ccd_preferred_size()`。
- `topology_plan_on()` 不再因 source mode 缺失而报错，改为标记
  `makeSourceMode`；并修了一个**跨 adapter 撞 id** 的真 bug：
  `_ccd_pick_mode` 现在按 `(infoType, adapter, id)` 查，source 占用判断也按
  adapter 分组。本机 VDD 的 src=0 与物理屏的 src=0 撞在一起，之前会挑到
  另一块显卡的 mode 项（就是上表第二行那个 87）。
- `_ccd_slice_bytes()` + 快照/回滚长度校验，修堆越界。
- 新增 `scripts/topology_mode_synthesis.py`（矩阵证据，只读）与
  `scripts/topology_live_synthesis_check.py`（真机端到端 + 自动还原）。

**真机验证（我自己跑的，不需要用户操作）**

```
样本：1589A778:00000000/9436/1337/0（VDD）
补齐后 modes：6 → 7 条  sourceModeIdx=[6]
SetDisplayConfig rc=0（67ms）
✅ 回读：已 ACTIVE
还原原始拓扑：rc=0   → VDD 回到 inactive
```

> ⚠️ 位置语义：断开后 Windows 不记得这块屏原来摆在哪（我们也不记账），
> 所以接回时统一放到**桌面最右边**。这和 Windows 设置里点"扩展这些显示器"
> 的行为一致。如果原来副屏在左侧，接回后会跑到右侧，需要手动拖一次。

### 2026-11-27 · v2.01.1：接回 rc=87 的真机定论（推翻我自己上一轮的诊断）

现场：点「点亮屏幕」弹出 `SetDisplayConfig 失败 rc=87`，events.log 里 15:50 / 15:59
一共 4 次「拓扑接回：现算 mode 被拒，降级为由 Windows 自选模式」。

**先说结论：上一轮我的两处判断都是错的。**

1. **那句日志是假的。** 它写在第 1 档（`use_plan_modes=False`）的 `else` 分支里，
   无条件输出——第 1 档成功时也会打出来。所以我看到的"现算 mode 总是被拒"
   根本不是事实。
2. **据此做的"修复"方向反了。** 我以为 87 来自"`QDC_ALL_PATHS` 的 233 条里
   有 224 条未连接组合，全量提交过不了 full solution 校验"，于是把接回改成
   `SDC_TOPOLOGY_SUPPLIED` 部分提交。实测**部分提交才是 87**，全量提交一直是好的。

真机定论（`scripts/topology_repro87.py`，全程只用 `SDC_VALIDATE`，不改拓扑）：

| # | 提交形态 | rc |
|---|---|---|
| C1 | 全量 233 条（含 224 条未连接组合），现状原样重交 | **0** |
| C2 | `sourceInfo.modeInfoIdx = INVALID` | **87**（加 `PATH_PERSIST_IF_REQUIRED` / `FORCE_MODE_ENUMERATION` / `ALLOW_PATH_ORDER_CHANGES` 全部无效） |
| C3 | `targetInfo.modeInfoIdx = INVALID`（source 有效） | **0** |

即：**source 侧 mode 是硬要求，target/desktop 侧可以交 Windows 定。**
老代码 `if None in (s, t, d)` 把**两个**都置 INVALID —— 这才是 rc=87 的唯一来源。

**修复**

- `_ccd_apply_plan_on()`：`sourceInfo.modeInfoIdx` 永不为 INVALID；只有
  `targetModeIdx` / `desktopModeIdx` 缺失时才把 target 侧交 Windows。
  source 缺失时**不改这条 path**（半改的数组提交上去就是 87）。
- `topology_plan_on()`：source mode 查不到时进 `errs`，在**计划阶段**就拒绝，
  错误文案写明"请重新插一次或重启，再接回"——不再硬提交换一个裸 rc=87。
- `topology_on()` 两档：`现算三元组` → `source 现算 + target/desktop 交 Windows`。
  删除 `SDC_TOPOLOGY_SUPPLIED` / `_CCD_PARTIAL_FLAGS` / `_ccd_plan_paths()`（已被证伪）。
- 降级日志只在**真的**被拒之后才写，且带上真实 rc。
- 新增 `scripts/topology_repro87.py`：上面三条结论的可复跑证据。
- 测试 95 → 102（新增 `TopologySubmitShapeTest`，钉住"source 绝不 INVALID"、
  "只有一种提交方式"、"降级必须真的被拒过"）。

> ⚠️ 仍未验证：**真实断开之后** Windows 会不会把那块屏的 source mode 从 modes
> 数组里丢掉。如果会，症状从"裸 rc=87"变成一句明确的
> 「锚点 … 的 source mode 不在当前 modes 数组里」。需要真机点一次
> 「熄灭屏幕」→「点亮屏幕」才能确认。

### 2026-11-27 · 设计纠偏 v2.01.0：程序不维护任何屏幕状态

> 用户纠偏原文："拓扑就是可以被修改的，这个软件的职责只是读取当前状态和调整当前状态。
> 它不需要维护任何状态。定时任务的话是可以在指定时间调整到指定状态的。"

这条纠正推翻了我此前把"上次操作结果"当成"当前事实"的设计，走查第 2 轮保留的
4 项全部是它的衍生问题。本轮按"现读 / 现算 / 不记账"重做。

**1) 删掉屏幕状态账**

- 删除配置项 `screen_state` / `screen_state_at` / `screen_off_tick`，
  以及 `mark_screen_state()`、`_screen_woke_by_input()`、`_merge_runtime_state()`、
  `screen_time_text()`、`coerce_setting` 的 `stamp` / `screenstate` 分支。
- `screen_verdict()` 重写：拓扑模式用 CCD 的 ACTIVE flag 当结论；`system`/`ddc`
  模式**不再给亮灭断言**——Windows 没有可靠的显示器电源读取接口（实测 `0xD6`
  在面板已黑时仍报 1），只有一个可信方向：**报了待机（2/3/4/5）才算灭**，
  其余一律 `unknown`，把寄存器值和"上次输入距今多久"摊给用户自己判断。
- `wake_options()` 的 `expect_off` 改为**当场探测**，不再读记账；
  `_render_states` / `_finish_screen_*` / `cli_screen` / 托盘 tooltip 一律不再回写状态。
- `save_config()` 回到纯原子写（不需要跨进程合并运行态，因为没有运行态）。

**2) 拓扑接回不再恢复历史快照（这是"第 1 个问题"本身）**

- 旧实现：断开时把整份 CCD 字节快照落盘，接回时原样恢复。期间用户在 Windows
  设置里改过的排布 / 分辨率 / 主屏会被旧快照覆盖 —— 典型的"程序替用户维护状态"。
- 新实现 `topology_plan_on()` + `_ccd_apply_plan_on()`：现读当前拓扑，
  按锚点挑一条**空闲 source**（未被其它 ACTIVE path 占用，取 id 最小者），
  再从**当前** modes 数组现算 `sourceInfo.modeInfoIdx = (srcMode<<16)|0xFFFF`、
  `targetInfo.modeInfoIdx = (tgtMode<<16)|desktopMode`。
- 真机实测支撑（`scripts/topo-declarative-probe.py --verify`，2026-11-27）：
  现算出的三元组与 Windows 自己填的值**逐位一致**
  （path#0 `0x0001FFFF/0x00000002`、path#1 `0x0004FFFF/0x00030005`）。
- 两级尝试：现算 mode 被拒时降级为 `INVALID` 交 `SDC_ALLOW_CHANGES` 由 Windows 挑。
  （**v2.01.1 推翻**：这一档把 source 侧也置了 INVALID，必然 rc=87，真机矩阵见上。）
- 快照只活在**内存**里，仅供本次操作失败回滚；`_ccd_save_snapshot` /
  `_ccd_load_snapshot` / `topology_snapshot_path` / `TOPO_SNAPSHOT_FILE` 全部删除。
- `topology_enable_anchor()` 不再检查"快照里有没有它"，只问当前拓扑 connected+inactive。
- `_ccd_wait_state()` 改为要求**每个**目标锚点都出现且状态一致，多屏接回少接一块
  不再误报成功。

**3) 走查第 2 轮保留的另两项（同源）**

- E2-04 `--force` 被 `enabled=False` 吞掉 → `ensure_task_registration` 的 force
  变成"对齐"语义，两个方向都做：启用则重建，未启用则清残留任务。
- E2-05 `ddc_allow_undeclared` 单向阀门 → 离开 ddc 方式时收回授权，
  避免"旧屏点过一次同意、换屏后风险被继承"。

- 新增 `TopologyPlanOnTest`（9 项）+ 改写受影响用例 → **92 tests OK**。
- 用户可见变化：非拓扑模式下状态卡不再显示"屏幕亮着/已熄灭"
  （读不到就不假装知道）；拓扑模式仍显示准确结论。

### 2026-11-27 · 场景走查第 2 轮（探索会话 + 收敛）

- 2 个 SBTM 探索会话（状态残留 / 少用入口）发现 16 项，新增 7 项回归测试 → **76 tests OK**。
- **2 个🔴已修复**：
  - E1-01 定时"亮度成功但熄屏失败"仍记 off/报成功 → 记账改用屏幕动作自己的 `sok`；
  - E1-02 GUI 陈旧内存整文件覆盖 CLI 刚写的屏幕状态 → `save_config` 按时间戳合并磁盘新值。
- 另修 5 项🟡：轮询链重入护栏、热键注册失败回滚、自动复亮 Timer 可取消、
  缺 Pillow 时 CLI 启动崩溃、caps 类型错误丢整份配置、models 坏键让 GUI 崩溃、热键 stop/restart 竞态。
- **保留 4 项需用户决策**（交互/CLI 语义/低风险）：拓扑快照过期、`load_config` 读中写盘、
  `--force` 在 disabled 被忽略、`ddc_allow_undeclared` 单向阀门。详见 `docs/walkthrough/report.md`。
- 验收：76 tests OK、REPO CHECK PASS。

### 2026-11-27 · 场景走查第 1 轮（scenario-walkthrough v3）

- 按仓库 `workbuddy-skills` 的走查方法，产出 `docs/walkthrough/`：
  能力清单（52 项 C-ID）/ 17 条素材剧本 + 覆盖矩阵 / 走查报告 / 坑库。
- **4 个🔴真缺陷 + 1 个命名混淆，全部修复并收敛到单点归属**：
  - P-01 托盘注册失败不落盘 → `_set_schedule_enabled` 单点写入 + `_rollback_schedule` 单点回滚；
  - P-02 注销失败残留任务继续熄屏 → 失败恢复 `enabled=True` 并落盘 + `cli_apply_schedule` 执行前复核 `enabled`；
  - P-03 屏幕状态 GUI/CLI 分歧 → 探测发现唤醒也落盘；
  - P-04 6 入口 4 种失败处理 → 5 个失败回调统一回滚，全仓 `cfg["enabled"]=` 只剩单点函数内 2 处；
  - P-05 `App.apply_schedule` 与模块同名 → 改名 `rebuild_schedule_tasks`。
- 回归 **69 tests OK**（原 62 + 7 项走查回归）；REPO CHECK PASS；
  `enabled=True` 时定时正常执行（用户已验收路径零改动）。
- 残余 13 项 🟡/⚪ 风险记录在 `docs/walkthrough/report.md`，按 skill 停止准则需第 2 轮收敛。

## [2.00.1] - 2026-11-27

> v2 首个版本：新增**拓扑显示方案**（把副屏从 Windows 桌面断开/接回，串流时只留 VDD）。
> 默认熄灭方式仍是 `ddc`，已验收的熄灭/唤醒路径零改动。

### 2026-11-27 · E3 动作层 + E4 配置与 GUI（拓扑显示方案）

- **E3 动作层**：主程序新增 CCD 写路径，但只在 `screen_off_method=topology` 下进入；
  默认 `ddc` / `system` 的熄灭与唤醒路径**一行未动**。
  - 断开：快照原始 `PATH_INFO` / `MODE_INFO` 字节 → 清 ACTIVE path（与 C2/C4 同口径）
    → `SetDisplayConfig`（`SAVE_TO_DATABASE`）→ 有界回读；失败自动回滚**一次**，
    错误码原样上报。快照落在 `%APPDATA%\MonitorBrightness\topology-snapshot.json`，不入仓。
  - 接回：恢复快照 → 回读 `ACTIVE`（≤2s 轮询）→ 写亮度；顺序按 D3，inactive 时不再写 DDC。
  - 护栏（D4）：VDD 永不作目标；主屏直接拒绝（实测 rc=87）；活动物理屏 ≤1 拒绝；
    一次只断一块；按 ACTIVE source 判定选中项，避免同源别名把主屏勾到副屏上。
  - 新单测 10 项（护栏 / 快照时序 / 回滚一次 / 快照失败不提交 / 档位亮度顺序），
    全部 monkeypatch，**不碰真机屏幕**；回归 **54 tests OK**。
- **E4 配置与 GUI**：`OFF_METHODS` 增加 `topology`（标签「拓扑断开」），
  设置页分段控件与说明文案同步；`SETTINGS.choice` 的值从 `OFF_METHODS` 派生，
  默认值仍 `ddc`。档位层在 topology 模式下 `off` 跳过亮度、`on` 把亮度交给
  `screen_on_cfg` 在回读成功后写。旧配置不写盘、继续可用。
- 跨机器验证按用户要求**暂缓**（E3 前置项未做完即进入实现）；E5 串流回归仍需用户执行。
- 验收：`REPO CHECK: PASS`、`KIT LOCK: PASS (v2.9.1, spec=2.1.5)`、56 tests OK。
- **接回亮度漏写修复**（同日，用户指出"断开的屏也应被检测"后复现的 E3 缺陷）：
  断开期间副屏不在 `EnumDisplayMonitors` 里，`apply_schedule` 算出的下标只有主屏；
  恢复快照后若照旧下标写亮度，刚接回的副屏拿不到亮度。改为按快照锚点**恢复后重新映射**
  下标（`topology_indices_for_anchors`，只认 ACTIVE source 的 gdi，不碰 aliases 串味），
  与旧下标取并集再写。新增 2 项回归单测 → **56 tests OK**。
- **GUI 显示断开屏**（用户点名要求，对齐 Windows 设置）：拓扑模式下列表额外列出
  `ACTIVE=0 但 targetAvailable=1` 的物理屏，灰显并带「启用」按钮；VDD 永不显示。
  断开/接回完成后自动重刷列表；`topology_enable_anchor()` 先校验快照覆盖该锚点，
  再走完整接回 + 写亮度。默认 `ddc` / `system` 的界面与列表一行未动。
  新增 4 项 GUI 单测（含隐藏 Tk 渲染断言「启用」按钮）→ **60 tests OK**。
- **计划任务清空**（用户要求）：`MonitorBrightness_Schedule` / `CatchUp` 已注销；
  复查 `Get-ScheduledTask` 已无 `MonitorBrightness*` 任务（其余系统任务未动）。
- **修复「应用亮度」界面异常**（用户提供 error.log：11-26 12:07 / 11-27 09:47 / 12:00
  三次 `TypeError: App._finish_apply() missing 1 required positional argument: 'before_xml'`）：
  根因是 `App` 里**两个同名 `_finish_apply`**——亮度回调 `(result, level)` 被任务重建回调
  `(result, part, before_xml)` 覆盖。亮度回调改名 `_finish_apply_level`，调用点同步；
  扫了全部类确认没有别的同名方法被覆盖。新增 2 项回归单测 → **62 tests OK**。
- 验收：`REPO CHECK: PASS`、`KIT LOCK: PASS (v2.9.1, spec=2.1.5)`、62 tests OK。

### 2026-11-27 · 按单项目规范 2.1.5 整理文档维护

- **文档拆分（阈值触发，先复制后删）**
  - `README.md` 264 → 118 行：余下内容进 `docs/使用指南.md`（定时机制 / 完整 CLI /
    完整 FAQ / 备选方案），主文件只留 环境要求 · 快速开始 · 高频 FAQ 4 条 · 文档索引
  - `DEVELOPMENT.md` 367 → 60 行：架构细节进 `docs/架构.md`，
    13 个历史问题拆成 `docs/问题记录/`（一坑一篇，模板与 knowledge-base 一致）
  - 新增 `docs/index.md` 作为文档总索引
- **修文档失真**（`REPO CHECK` 查不出这类问题）
  - 架构图里的 `monitorcontrol`（v1.7.0 起已直连 dxva2）、`apply_slot()` /
    `catch_up_decision()`（v1.9.4 已删）、6 个不存在的函数名
  - 坑位条数「15 / 16 条」→ 实际 **60 条**
  - 删掉「`docs/` 下三篇分析文档目前没有入口」—— README 早已链上
- `AGENTS.md` 新增「边界」节：声明 link-only 只查链接 / 锚点，语义正确性靠人审
- **机器层**：repo-kit 2.7.2 → **2.9.1**，spec 2.1.3 → **2.1.5**
  （按上游口径先手工替换带 BOM 的 `update-repo-kit.ps1`，再跑安装器；
  `schema/taxonomy.json` 的 version 同步，否则 fleet 审计报 skew）
- **CI**：新增 `tests` job（windows-latest）跑 35 项回归 + Tk 探针 + `skipped` 假绿拦截；
  不设 `continue-on-error`（它会让失败的 job 仍报绿，观察期等于盲区）
- **测试去墙钟化**：两个 GUI 回归用例原本用 `time.time() + 0.3` 轮询弹窗（Fowler 的非确定性
  清单里「时间」和「异步」两坑）。实测弹窗要么在点击调用内同步创建、要么经 `after(0)`
  投递，**1 次 Tk 事件循环就 mapped**，完全不用等真实时间。改成固定轮数 `pump_ui()`：
  CI runner 上 Tk 初始化实测 810ms，旧的 300ms 时限是潜在的 flake 源。本地连跑 10 次全绿
- **阶段 0 只读探针**：新增 `scripts/topology_probe.py`（无第三方依赖，只查不写）与
  `docs/拓扑探针.md`。本机实测：`QDC_ALL_PATHS` 返回 **233 条 path 却只有 3 块屏**
  （2 物理 + 1 VDD）；`DISPLAYn` **不唯一**（同一块屏对应 4 个 source）；
  稳定锚点（显卡 LUID + EDID mfr/prod + `connectorInstance`）唯一区分两块同型号屏；
  VDD 可单独识别；只有 ACTIVE 的屏能读到 DDC
- **C2/C3/C4 真机实测**（scripts/topology_switch.py，两屏交替、任一时刻最多灭一块）：
  副屏断开 rc=0 / 全程 805ms、接回 986ms 且 DDC 恢复；**主屏断开 rc=87，Windows 不允许**
  （已加护栏）；计划任务（Interactive principal）内 CCD 查询 `rc=0`，可用。
  结论：有界轮询取 2 秒足够；主屏不能直接作为断开目标
- **C5 回滚验证**：`--force-verify-fail` 让"提交成功但回读失败"真实发生，原始字节快照
  自动恢复成功，两屏仍 ACTIVE → 回滚路径可用，不会卡在半途状态
- **阶段 D 设计定稿**：写进 `DEVELOPMENT.md`，6 条决策（稳定锚点 / topology 选项 /
  档位顺序 / VDD+主屏护栏 / 旧配置迁移 / 快照回滚）+ E1–E5 实现顺序；
  新增一坑一篇 `docs/问题记录/topology-primary-rc87.md`
- **持久化与副作用实测**：新增 `scripts/topology_side_probe.py`（只读）。
  `QDC_DATABASE_CURRENT` 在断开中变为 `topologyId=1 / active=1/1` → `SDC_SAVE_TO_DATABASE`
  确实写入数据库。窗口副作用量化：off 时 3 个窗口被移动、6 个消失；接回后 17/19 回原位、
  5/6 回来 → 不保证完全恢复，文档已写明
- **待办**：跨机器验证（不同显卡 / 单屏 / Python 3.10~3.12 / 32 位 / 不同 Windows 版本）
  已写进 `DEVELOPMENT.md`，用户指定稍后执行；不阻塞 E1/E2
- **E1 只读层接入**（主程序第一次改动，仍不碰开关）：
  - 主脚本内新增 CCD 只读查询（`ccd_probe_topology`）：按官方 `QueryDisplayConfig` /
    `DisplayConfigGetDeviceInfo` 字段构造，按锚点折叠同一块屏的多个 source；
    `edidIdsValid` 按官方 bit 2 判断，`targetAvailable=0` 的瞬态路径直接过滤
  - `screen_verdict` 仅在 `screen_off_method=topology` 时把拓扑 ACTIVE 当结论；
    默认 `ddc`/`system` 逻辑与文案**完全不变**（有单测守着）
  - `SETTINGS` 新增 `monitor_anchors`（kind=anchors，E2 迁移用）
  - `--diag` 新增"四、显示拓扑"段：每块屏的 ACTIVE / 锚点 / GDI 名 / 同源别名
  - 实测 `QDC_ALL_PATHS` 约 0.10ms（233 条 path），折叠后 3 块屏；39 tests OK
- **E2 身份层迁移**：`monitor_anchors` 成为第一优先，`monitor_ids` / `monitors` 依次兜底；
  任一层没命中就继续往下走，绝不静默清空用户选择。新增 `selection_anchors()`，
  GUI 两处保存路径（勾选记忆 / 定时保存）都写入锚点；VDD 锚点永不参与解析。
  `--diag` 打印锚点与解析结果。真机闭环验证：勾选 0,1 → 两个 EDID 锚点 → 解析回 [0,1]
- 验收：`REPO CHECK: PASS`、`KIT LOCK: PASS (v2.9.1, spec=2.1.5)`、44 tests OK

### 2026-11-17 · 接入单项目规范机器层

- 接入 knowledge-base 的**单项目规范**（spec 2.1.3 / repo-kit 2.7.2，profile `link-only`）
- 新增 `schema/taxonomy.json`、`tools/repo-kit/`、`tools/update-repo-kit.ps1`、`schema/kit.lock.json` 与 CI
- 只查链接与入口，**不要求 frontmatter**
- 10 个相对链接纳入检查
- **不新增 `requirements.txt`**：本仓是 Python 应用但无依赖声明，只含文档依赖的清单会误导；CI 改为显式安装
- 验收：`REPO CHECK: PASS`（ERROR=0）、`KIT LOCK: PASS (v2.7.2, spec=2.1.3)`

## [1.9.7] - 2026-11-04

### 新增（0x60 切源翻转的快速点亮）

- 现场依据：两次独立实测（Legion R27qe Gen2 / DISPLAY2，16:57 与 17:00），
  `0x60` 读当前源 → 写 17 → **立即**写回原值：单台 182~188ms，两次都点亮，
  读回 `0x60` 均回到 15。点亮来自「离开当前口再回来」的边沿（DP 接收端重跑链路
  训练并重锁时序），与停在中途多久无关，所以本路径**不 sleep**。
- 新增 `screen_wake_entry(cfg, indices)` 作为点亮统一入口（GUI 点亮、CLI
  `--screen on`、计划任务 `apply_schedule` 都改走这里）：
  - `wake_fast_flip`（默认 1）+ `screen_off_method=ddc` + `wake_switch_input` +
    记账认为屏熄过 → 走 `screen_wake_flip()`，逐台 `0x60` 翻转，单台 ~180ms；
    全选/部分选择都只动勾选的屏，**绝不发 SC_MONITORPOWER 广播**。
  - 其余情况（开关关闭、system 模式、屏亮着、禁用切源）→ 原样调用
    `screen_on_cfg()`，v1.9.6 行为不变。
  - 同一台 8 秒内第二次触发点亮 → 该台升级为 `_wake_one()` 完整阶梯，
    状态行提示「已升级为完整点亮」。
  - 读不到当前源 / 无可用中间源 / 写失败 → 回退完整阶梯；写回失败会立即补写一次
    并读回校验，避免把屏留在中间源。
- 中间源选择：优先 17；当前就是 17 时取能力串已声明的另一个口（优先 15）。
- 旧配置缺 `wake_fast_flip` 按默认 1 处理；`CONFIG_VERSION` 不变（读侧逐键 coerce）。
- `_wake_one` / `screen_off_cfg` / `screen_on_cfg` / `_switch_input_roundtrip` /
  `_output_recycle` / `screen_wake_many` / `wake_options` 与 v1.9.6 逐字节一致
  （见 `_mb_func_diff` 验证），本轮只新增路由层与新函数。
- 单测 +9：翻转序列无等待、当前值 17 的中间源、8 秒二击升级、8 秒二击的完整阶梯、
  门控（屏亮/禁用切源/system 模式）、全选零广播、开关关闭回退、旧配置缺键兼容。

## [1.9.6] - 2026-11-04

### 修复（点亮误伤未勾选显示器）

- 根因：点亮第 3 级 `_output_recycle()` 调用 `SendMessageTimeoutW(HWND_BROADCAST,
  SC_MONITORPOWER, ...)`，这是**系统级广播**，会黑掉所有显示器；而且每个被选中的
  显示器各执行一次，2 台就是 2×OFF + 2×ON。
- 修法（只加过滤层）：`screen_wake_many()` 计算 `allow_recycle = 选中台数 == 总台数`，
  传给 `_wake_one(..., allow_recycle)`；只勾部分时**跳过系统级广播**，仅对勾选的
  那台发 `0xD6=1` / 写亮度 / 输入注入 /（允许时）切输入源。
- 全选时 `allow_recycle=True`，调用序列、sleep、广播次数与 v1.9.5 完全一致。

### 修复（空勾选）

- `screen_on` / `screen_off` 在 `selected_indices()` 为空时不再落到 `_select` 的
  “空=全部”分支，改为状态行提示「请先勾选要操作的显示器」，不发任何命令。

### UI

- 「任务计划」下方事件行 `event_lbl` 由橙色 `#ff9f0a` 改为辅助灰 `#6c6c70`
  （与 `task_sub`/`task_next` 同色）；`pady`、字号、wraplength 均未动。
- 该行若包含「失败/错误/异常/未就绪」字样，仍保留橙色，避免把告警灰掉。

### 验证

- 干跑序列（写/广播打桩、读与 sleep 真实）：只勾 1 台 → 仅 DISPLAY1 4 次写
  （`0xD6=1`、`0x10=80`、`0x60=17`、`0x60=15`），广播 **0** 次，3167ms；
  勾 2 台 → 两台各 4 次写，广播 4 次（2×OFF + 2×ON），4029ms。
- 真机（仅 DISPLAY1，`allow_switch=True`）：4 次写全部落在 DISPLAY1，
  广播 0 次，**3406ms**；DISPLAY2 全程 0 次写、状态读回前后不变；
  DISPLAY1 输入源已写回 15，亮度 80、电源 1 与测试前一致。
- 26 项单元测试通过（新增 3 项），`--selftest` 通过。

## [1.9.5] - 2026-11-04

### 修复（仅 C 项：忙时点击被静默丢弃）

- `_busy` 窗口内（点亮约 4.03s）点「熄灭/点亮」或用热键，原来直接 `return`，
  无日志无提示。现在：
  - 状态行立刻显示「正在唤醒中…（或 正在熄灭中…）点亮/熄灭请求已排队，完成后自动执行」；
  - 记住**最后一次**请求（连点只保留最后一个意图），当前忙任务结束后补执行一次；
  - `events.log` 记录「忙时…请求已排队」与「补执行忙时排队的…请求」。
- **未改任何时序**：`_wake_one`、`screen_off_cfg`、`screen_on_cfg`、sleep 时长、
  `_busy` 窗口长度、失败回退行为全部保持 v1.9.4 原样；补执行在忙任务回调结束时
  触发（受现有 150ms 轮询节拍约束，最多晚 150ms）。

### 明确不做（用户裁定）

- **A 取消**：不改熄灭值，不把 `0xD6=2` 替换成能力串声明的 `04/05`，不加
  「用声明值替换硬编码值」的逻辑（`ddc` 熄灭在这台机器上一直可用）。
- **B 不做**：不做分级升级、不改 sleep、不加早退判据；**亮屏逻辑与 v1.8.5 一致**
  （固定阶梯属基线固有行为，不是本次回归）。

### 验证

- 函数级 diff：`_wake_one` / `screen_off_many` / `screen_wake_many` /
  `_switch_input_roundtrip` / `_output_recycle` / `_finish_screen_off` /
  `_finish_screen_on` / `run_bg` 与 v1.8.5 **逐字节一致**；仅
  `screen_off` / `screen_on`（新增 `_busy_action` 赋值）与 `_poll_read_queue`
  （新增排队补执行）有差异，均在 GUI 层。
- 23 项单元测试通过（新增 4 项：忙时提示/排队、只补执行一次、轮询顺序、空闲点击）；
  `--selftest` 通过。
- 部署：覆盖 `D:\Tool\MonitorBrightness.exe`（部署前后体积/SHA256 见交付记录）。

## [1.9.4] - 2026-11-04

### 变更（用户对三项待定的裁定）

- **启动自检 = 只检查任务配置**：保留读 `System32\Tasks\*` XML、比对路径/参数/触发点/
  `StartWhenAvailable`、不一致则原地重建；**删除** v1.9.0 新增的 in-app 补执行、
  12 小时补检阈值与 `last_fired` 记账。配置一致时状态行只显示
  「定时任务已就绪，下次运行 X」，不弹窗。**本程序启动时不会主动熄屏。**
  （v1.8.5 已有的登录校准任务 `MonitorBrightness_CatchUp` 保持不动。）
- **撤销 DPI 感知**：删除 `SetProcessDpiAwarenessContext/SetProcessDpiAwareness/
  SetProcessDPIAware` 三档回退；`ScrollArea` 恢复 `winfo_screenheight() - 180` 的封顶逻辑；
  `--diag` 无 DPI 输出，保持原样。
- **保留旁路增强**（均不在熄灭/点亮调用链）：`events.log` + 界面事件行、
  `woke_by_input` 只做内存记账（删掉探测回调里的 `save_config`）、
  `_fetch_models` 线程内销毁句柄（其 `join(20)` 是 v1.8.5 原有等待，未新增）。
- **`apply_schedule` 恢复 v1.8.5 语义**：不再写 `last_fired`，只由计划任务
  （`--apply-schedule`）调用；GUI 启动自检不再调用它。

### 验证（不触碰屏幕）

- 30 项函数级 diff：26 项与 v1.8.5 **逐字节一致**；4 项（`_capabilities_cached`、
  `_luminance_max`、`_switch_input_roundtrip`、`screen_off_many`）仅缓存 key 不同
  （`DEV#EDID`，用户点名保留）。
- 熄灭/点亮调用链内无 `_write_event_log`、无新增 `join`/`sleep`；
  `_write_event_log` 只出现在配置恢复、任务重建、界面异常兜底、时间段重写四处。
- 19 项单元测试通过；`--selftest` 通过。
- 部署：覆盖 `D:\Tool\MonitorBrightness.exe`（部署前后体积/SHA256 见交付记录）。

## [1.9.3] - 2026-11-04

### 回滚（用户裁定：只修被复现的 bug，不许改已验证的操作逻辑）

- **熄灭方式默认值恢复 `ddc`**：撤销 v1.9.0 把默认值从 `ddc` 改成 `system` 的改动。
- **撤销「旧配置 ddc→system」自动迁移**：不再静默改用户配置；被迁成 `system` 且
  未允许未声明值的配置，加载时反向恢复为 `ddc`，并把「system → ddc」写进事件日志。
- **点亮恢复 `mouse_event` 1px**：撤销 `SetThreadExecutionState(ES_DISPLAY_REQUIRED)`，
  恢复 v1.8.5 的 `_nudge_user_input`（点亮命令后移 1px 再移回，用于把系统从"显示器关闭"
  状态踢回有显示）。
- **唤醒句柄生命周期恢复 v1.8.5**：撤销"谁用谁销毁 + 全局 30 秒 deadline"，
  恢复线程 `join(30)` 后统一 `release_targets(targets)`。
- **保留**（用户点名保留）：能力串缓存 key 的 `DEV#EDID` 修复、任务路径重建、
  `parse_hhmm` TypeError、静默异常写入 error.log/状态栏、config 原子写、Pillow 守卫。

### 验证（不触碰屏幕）

- 函数级 diff：`_broadcast_monitor_power` / `screen_off_system` / `screen_on_system` /
  `_nudge_user_input` / `_wake_one` / `_output_recycle` / `screen_off_cfg` / `screen_on_cfg` /
  `wake_options` / `mark_screen_state` / `_write_vcp` / `_read_vcp` / `enumerate_targets` /
  `release_targets` / GUI 的 `screen_off`/`screen_on`/`run_bg` 与 v1.8.5 **逐字节一致**；
  仅剩缓存 key 差异（用户点名保留）。
- 干跑（写操作全部打桩）：ddc 熄灭 = 每台 1 次 `0xD6=2`，2 台共 1.4 ms；
  system 熄灭 = 1 次 `SC_MONITORPOWER` 广播；与 v1.8.5 调用序列一致（1.7 ms / 0.5 ms）。
  点亮序列与耗时（~4.0 s，含固定的 0.35/0.8/2 s 等待）与 v1.8.5 一致。
- 22 项单元测试通过（新增默认值/反向恢复断言）。

## [1.9.2] - 2026-11-04

### 新增（用户要求：任务重建成功要有明确提示）

- **新增「应用」按钮**：定时卡片状态行加了显式的「应用」，按当前时间段强制重建
  计划任务（旧版只有切换开关或改时间段才会写任务，想"照现在配置再来一遍"没入口）。
- **成功确认弹窗**：手动「应用」/ 开启「启用每日定时」重建成功后，弹自绘深色窗
  （1px 描边、圆角、主色「确认」按钮），正文含触发档位数与具体时间、下次运行时间、
  本次是否更新了任务路径/触发点、登录校准状态；点「确认」关闭。不是原生 messagebox。
- **失败不弹**：重建失败仍走 `showerror` + 状态栏，不伪装成功。
- **启动自动重建不弹窗**：开机自检每次都可能自愈（换 exe 目录尤其常见），弹窗会糊脸；
  反馈改走状态卡 + 事件日志，日志现在带"档位数 + 下次运行时间"。
- **线程**：`run_bg` 完成回调再 `root.after(0, …)` 回 Tk 线程弹窗，不在后台线程碰 UI。

### 修复（截图取证时发现的 Tk 合成输入问题）

- Tk 的 Windows 端口会用线程键盘状态里的 `VK_LBUTTON` 过滤 `PostMessage` 合成鼠标消息；
  取证脚本改为按下/抬起各设一次 `SetKeyboardState`（只影响本线程），否则点击被静默丢弃。
  弹窗本身是普通 Toplevel + 子控件，没有 `UpdateLayeredWindow`，按钮由 DWM 正常合成。

## [1.9.1] - 2026-11-04

### 修复（真机反馈：点「添加时间段」仍无弹窗，窗口像没反应）

- **根因（具体到行）**：`parse_hhmm()`（`monitor_brightness.py:1450`）返回的是
  "当天分钟数 int"，而 `SchedulePanel.add_row()`（`:3699`）写成
  `hh, mm = parse_hhmm(...)`，点击瞬间抛
  `TypeError: cannot unpack non-iterable int object`。v1.9.0 只是把按钮从禁用
  改成可点，第一次真正执行到这条路径；windowed 打包后 `sys.stderr` 为 `None`，
  Tk 回调异常被静默吞掉，所以界面看起来"完全没反应"。
- **改法**：`add_row` 按分钟数直接 `(m + 60) % 1440` 递增；同时给根窗口安装
  `report_callback_exception`，异常写 `%APPDATA%\MonitorBrightness\error.log`，
  并在状态栏显式显示"界面异常：…"，不再出现点了没反应的黑洞。
- **回归**：`AddButtonRegression` 改为真实 `SlotEditor`：点击后 300ms 内断言
  Toplevel `winfo_ismapped()` 且宽高 > 0；再在弹窗填 `07:20 / 60%` 保存，
  断言列表 6 档、config 落盘、弹窗关闭。
- **截图取证**：独立桌面 + `PrintWindow`，不抢前台、不移动真实光标：
  `proof_v191/01_before_click.png`、`02_dialog_visible.png`、`03_after_add.png`。
- **构建**：`APP_VERSION` 1.9.1，`VERSION_DIR=dist_v191`。

## [1.9.0] - 2026-11-04

### 修复（用户反馈：定时没准时执行）

- **根因（运行取证）**：任务 `\MonitorBrightness_Schedule` 只有用户手动点「应用」才重建；
  exe 每版换目录（`dist_v185` → `dist_v186`）后 action 仍指旧路径，任务"跑了"却找不到
  程序（`LastTaskResult` 0x80070002）。而每次重建又是先 `Unregister` 再 `Register`，
  把 `LastRunTime` 重置成"从未运行"，且新触发的 `StartBoundary` 从"今天"算起，
  当天已过去的档位不会再触发。
- **改法**：启动时读落盘的任务 XML，比对程序路径 / 参数 / 全部触发点 / `StartWhenAvailable`，
  不一致就**静默重建**（后台线程，写事件日志）；已存在时优先 `Set-ScheduledTask` 原地更新
  （尽量保留上次运行记录），失败才回退卸载重注册。新增 `--ensure-schedule` 供无界面自愈。
- **改法**：时间段增删改后立即落盘；定时已启用时 500ms 防抖自动重写任务
  （旧版只有开关切换才保存，改完时间不关开关触发器还是旧的）。
- **界面**：任务状态行现在显示**下次运行 / 上次运行 / 上次结果**（`0x0` 成功、
  `0x41303` 从未运行），失败不再是"沉默的"。

### 新增（用户反馈：打开程序先补检一次）

- 窗口显示后 900ms 在后台线程判定：今天已到点的最后一档若错过 ≤12 小时、且
  `last_fired` 不是"今天 + 这一档"，就补执行一次。12 小时的取舍：覆盖前一晚关机、
  早上开机的场景；更久的档位意图已过期，下一档往往就在眼前。
- 防重复：`last_fired`（日期 + 档位 + 秒级时间戳）由计划任务与补检共同写入；
  保存时与磁盘记录取较新者，避免常驻 GUI 用旧内存值覆盖任务刚写的记录。
- 事件日志 `%APPDATA%\MonitorBrightness\events.log`，界面「定时」卡下方直接显示
  `今天 08:15 补执行 08:30 档（错过 31 分钟）`、`计划任务已按当前程序重建` 等记录。

### 修复（用户反馈：「添加」按钮没反应）

- **根因（最小复现）**：`render()` 把「＋ 添加时间段」的可用状态与"启用每日定时"开关绑定，
  默认关闭时按钮置灰；自绘 `Button` 在禁用时点击静默 `return`（连提示都没有）。
  仅在"定时未启用"或"正在注册"时无效。
- **改法**：添加/编辑始终可用，只受执行中的忙碌门闩影响；新增回归测试
  （隐藏 Tk 下点击 → 列表 +1 → config 落盘）。

### 变更（用户反馈：左右滑块不好用）

- 定时时间控件改为**可直接键入** `HH:MM`（接受 `9:5`、全角冒号、`0905`）+ ▲▼ 15 分钟步进
  + 四个常用预设（08:30 / 12:00 / 18:00 / 21:00），键盘 ↑/↓ 也能步进。
- 配置 schema 不变（`slots[].time/level/screen`），旧的 `day_time/night_time` 迁移、
  `9:5` 这类写法统一规整为 `09:05` 并去重。

### 稳定性（复核清单中判定"必须修"的项）

- **DPI**：启动前声明 per-monitor v2（失败回退 per-monitor / system aware）；
  `ScrollArea` 限高改用当前显示器 `rcWork`（扣任务栏），不再用主屏全高 -180。
- **配置**：`config.json` 改原子写（同目录 mkstemp + `os.replace`）；读失败不再静默，
  保留 `config.json.bad-*.json` 副本并在界面显式提示。
- **缓存**：能力串 / 亮度上限的 key 改为 `DEV#EDID 短哈希`，换面板不再沿用旧缓存；
  读不到 EDID 时退回旧行为。
- **Pillow**：改为守卫 import。缺 Pillow 时 CLI 全部可用，GUI 明确提示后退出。
- **点亮**：移除 `mouse_event`（真实移动光标 1px），改用
  `SetThreadExecutionState(ES_DISPLAY_REQUIRED)`，不产生任何输入事件。
- **句柄生命周期**：唤醒/型号线程改为"谁用谁销毁"句柄，`join(30)` 超时后不再
  `DestroyPhysicalMonitor` 仍在被线程使用的句柄；超时会在结果里明确说明。
- **默认熄灭方式**：`ddc` → `system`。旧默认值与 `ddc_allow_undeclared=False` 组合会
  让新装用户点「熄灭」被"面板未声明待机"闸门全部跳过（按钮像没反应）。读取旧配置时，
  这条组合（只可能是没动过的默认）自动迁移为 `system`；主动选过 DDC 的用户
  （确认框会置 `ddc_allow_undeclared=True`）保持原选择。

### 构建

- `APP_VERSION` 1.9.0，`VERSION_DIR` 改为 `dist_v190`；新增 `tests/`（`python -m unittest`，18 项，不碰硬件）。

## [1.8.5] - 2026-11-03

### 修复（用户反馈两项）

- **修：时间段行鼠标扫过文字就消失**。根因：行复用了自绘 Button，行内容是
  "额外"画上去的，而 Button 的 hover 事件会 `delete("all")` 重画底色——
  把自定义文字一起抹掉。修复：新增 `_SlotRow(Button)` 子类，重写 `_draw`
  让底色和内容同一次绘制（hover 重画天然完整）；定时未启用的整行变暗
  也改走 `set_dim()`，旧版找子 Label 的置灰循环删除。
- **修：双显示器上弹窗先闪现在别处再跳到主窗前**。根因：Toplevel 创建后
  先被窗口管理器按默认级联位置显示一帧，`show()` 里才 `geometry()` 挪过来。
  修复：Sheet 与 DarkBox 一律**创建即 `withdraw()`，定位完成 `deiconify()`**
  —— 弹窗只出现一次，直接在主窗中心。六项回归全过（hover 前后文字一致、
  置灰/恢复、设置/编辑器/消息框定位与显示）。

## [1.8.4] - 2026-11-03

### 修复（用户反馈"熄灭屏幕时间太久" → 实测定位）

- **根因（实测）**：DDC 熄灭前按 v1.7.x 的能力串闸门逐台读能力串，而能力串读
  实测 **3.1 秒/台**（VCP 读写各只要 62ms）——两台串行 6.3 秒，占了熄灭耗时的 97%。
- **修复**：能力串是面板的**静态固件属性**，按设备名缓存并随配置持久化
  （load 播种 / save 写回，换显示器后首次仍会实测刷新）。
  实测：冷启动（含实读）6163ms → 热路径 **1ms**（写打桩下），实际熄灭只剩
  每台一次 0xD6 写入（约 125ms）。
- `monitor_model` 与点亮时的信号源选择同样走缓存（型号显示也因此秒出）。
- 能力串闸门（未声明 02 不盲写）逻辑不变，只是不再重复读。

### 构建（用户问"exe 还能更小吗"）

- **32MB → 15MB（-53%）**。用 archive viewer 取证发现两条"鲸鱼"：
  1. **numpy 全家桶（含 20.5MB 的 OpenBLAS DLL）** —— 被 PIL 的 `_typing` /
     `Image.__array_interface__` 等可选依赖静态引入，运行时从不执行
     （`TYPE_CHECKING` 分支 / 函数内懒导入），排除完全安全；
  2. **AVIF 插件（4.3MB 的 `_avif.pyd`）** —— 只创建内存位图的本程序用不到。
  另排除 Pillow 用不到的编解码插件与 freetype/lcms/webp（约 -2MB）。
  安全性：PIL 对插件缺失的设计就是"静默跳过"；打包脚本 `_build.py` 已固化
  排除清单，后续打包自动生效。验证：GUI 启动、AA 渲染、CLI 均正常。
  （如需再压可上 UPX 到 ~11MB，但有杀软误报风险，未采用。）

## [1.8.3] - 2026-11-03

### 变更（用户反馈两项）

- **主界面不再出现任何 DDC 字样**（"太深奥了"）：单屏待机模式下状态卡的
  "圆点 + 标题 + 时间"整行隐藏，只留提示行（亮度/恢复提示）与操作按钮；
  设置里「DDC 单屏待机」改名「单屏待机」，相关提示/错误文案同步改写。
  DDC 的完整解释只保留在「说明」弹层。
- **修：定时卡下缘贴窗口下边**。在滚动内容末尾加 10px 底部留白
  （实测卡底到窗口底正好 10px）。

## [1.8.2] - 2026-11-03

### 修复（用户反馈"响应速度不正常" → 检索 + 实测定位）

- **根因（实测）**：v1.8.1 把"探测亮灭+读亮度"合并进 `read_current`，而它会在
  每次应用亮度/切换勾选后自动执行 —— DDC 事务量从 1 读/屏（124ms）涨到
  3 读/屏（371ms，实测 2 屏）；再叠加每次探测后的 `geometry("")` 整窗重排。
  渲染层实测无恙（启动 470ms、滑块 50 步 18ms）。
- **自动刷新回归轻量路径**：`read_current` → `refresh_brightness()`（1 读/屏），
  全量探测（371ms）只留给「检测」按钮、启动和熄灭/点亮状态切换点。
  实测：切换勾选 UI 即时响应（读走后台线程）、应用预设 435ms。
- **修：启动时状态卡停在默认值**。轻量刷新与全量探测共用 `_probing` 标志，
  启动时前者占住标志导致后者被守卫静默跳过（DDC 模式下显示"屏幕亮着"而非
  「DDC 单屏待机」）。改用独立 `_brighting` 标志。
- **`_fit_window` 防抖**：合并 80ms 内的连环触发；窗口请求高度没变就跳过
  `geometry("")`（整窗重排不便宜且会闪）。
- **卡片渲染守卫**：尺寸没变不重画（防 `<Configure>` 反馈循环互相喂事件）；
  滑块填充宽度量化到 4px，拖动不再逐像素生成新贴图。
  依据：tkinter 图片性能共识——"UI 卡顿多因在 resize/mousemove 等高频事件里
  重采样而缺少防抖与缓存"；实测取证后才动手。

## [1.8.1] - 2026-11-03

### 变更（用户反馈四项）

- **默认熄灭方式改为 DDC 单屏待机**（`screen_off_method` 默认值 system → ddc）；
  配置里同时打开 `ddc_allow_undeclared`（本机两台面板实测可唤醒）。
- **DDC 模式下状态卡不再显示"亮着/熄灭"结论**：`0xD6` 回读不可信（本机实测会撒谎），
  状态卡改为显示「DDC 单屏待机」+ 灰点，右侧时间结论一并隐藏；
  熄灭/点亮两个按钮都可用（不再按结论互斥置灰）；亮度提示行保留。
  切回「系统级」即恢复亮灭结论显示。
- **主界面重排**：显示器选择移到最上面，屏幕控制（状态卡）下移到其后面，
  之后依次是亮度、定时。
- **修复：定时区下边缘被窗口裁切**。根因是窗口 `resizable(False)` 后 WM 不跟随
  异步晚到的内容（任务状态行、新增时间段）长高。修复：`ScrollArea.refresh()`
  触发 `App._fit_window()`（`geometry("")` 按请求尺寸重排），内容超高仍由
  滚动区封顶兜底；封顶余量从 140px 加到 180px。

## [1.8.0] - 2026-11-03

### 变更（界面按 Apple HIG 重构：先出 HTML 设计稿 → 按"上个版本 + 设计稿"重做）

设计稿：`design/ui-mockup.html`（可交互，含状态切换演示与两个弹层）。

**主界面只留四块**（对照设计稿逐项落地，删除了全部旧交互残留）：

- **状态卡**：`● 屏幕亮着 · 15:11 点亮`（短格式）+ 常驻提示行。
  「熄灭 / 点亮 / 检测」三按钮随状态切换主次并自动置灰；检测动作合并了
  亮灭结论与当前亮度读取（一次后台任务），提示行显示 `当前 75% · 2 台显示器`。
- **显示器**：圆角勾选框 + 型号 + `分辨率 · 主/副 · DISPLAYx`；
  分组标题行右侧「全选 / 全不选 / 刷新」轻链接。
- **亮度**：自绘细轨道滑块 + 百分比，**松手即写回**选中显示器（拖动过程不刷 DDC）；
  预设为分段控件，当前值匹配时高亮。**删除了「应用到选中显示器」「读取」
  「当前亮度：…」整行** —— 设计稿中没有这些。
- **定时**：药丸开关「启用每日定时」，**切换即注册 / 注销任务计划**
  （注册失败自动把开关拨回去）；时间段列表点行弹编辑器（时间 / 亮度 /
  到点熄屏点亮 / 删除）；底部任务状态行
  `● 任务计划：已启用 · N 个触发点 · 补跑已开 · 下次 21:00 · 检测`。
  **删除了「保存并启用定时」「取消定时」「测试当前档」三个按钮** ——
  开关本身承担保存/取消，测试档属于旧交互。
- **删除底部状态栏**：所有操作反馈改为状态卡提示行的**临时消息**
  （闪 3 秒回到常驻提示，`status_var` 经 trace 统一接入）。
- **弹层**：「设置」（托盘 / 最小化到托盘 / 自启 / 热键 / 熄灭方式 / 自动点亮 /
  切信号源 / 预设数值 / 关于）与「说明」（状态判据 / 两种熄灭方式 / 唤醒阶梯 /
  排查命令）只从标题栏右侧进入，主界面不再出现任何解释性文字。
- **控件全部自绘**：圆角卡片、三态圆角按钮（含置灰）、药丸开关、分段控件、
  细轨道滑块、圆角勾选框（逐行按圆方程内缩绘制，四角透明）。

### 变更（用户反馈"圆角和设计稿差太多" → 检索后整体换渲染管线）

- **根因（检索交叉验证）**：Tk Canvas 在 Windows 上没有抗锯齿 ——
  CustomTkinter 官方 README 明说"只有 macOS 有"；`smooth=True` 多边形只能改善
  曲率（Stack Overflow 权威答案），消除不了锯齿。矢量画圆角到不了设计稿的质量。
- **方案**：全部圆角改用 **Pillow 4× 超采样渲染 + LANCZOS 缩小 + 带 alpha 的
  PhotoImage 贴图**（Tk 8.6+ Canvas 原生支持图片 alpha；Pillow 本就是依赖）。
  覆盖：卡片、按钮、开关、分段控件、滑块轨道/手柄、状态圆点、时间段 chip、
  勾选框（对勾改为带 joint 的折线）。矢量圆角代码（`_round_rect`、四角啃角）
  全部移除。
- 卡片从 Frame 改为 Canvas 承载：内部 Frame 用 `create_window` 挂载，
  尺寸按内部请求值显式设置（Canvas 不传播子控件尺寸）；
  渲染结果按参数缓存（`_AA_CACHE`），引用全局持有（`_AA_REFS`）。

### 修复（重构中踩到的，已记入 AGENTS.md）

- **`self._w = width` 会覆盖 tkinter 的控件路径**（`Misc._w`）→ 之后建子控件直接
  `TypeError: unsupported operand type(s) for +: 'int' and 'str'`。改名为 `_width`。
- **Toplevel 弹层没有强引用会被 GC 连窗口一起收走** → 必须由 App 持有；
  否则"点了设置没反应"。已用 `self._sheet` / `self._editor` 持有。
- **弹层尺寸必须在内容搭完后再设置**：基类构造时 body 还是空的，量出来只有 144px，
  被 `resizable(False)` 锁死后内容全被裁掉。改为子类末尾调用 `show()`。
- **控件必须在目标父容器里创建**：先 `Switch(卡片)` 再 `row.pack(...)` 会跑到卡片其它位置，
  已改成 `row(..., builder=lambda p: Switch(p, ...))` 的写法。
- **测试脚本把每个步骤调度两次会制造假阴性**（链式 `after` + 顶层 `after` 各一次）→
  验证异步流程时只允许一个调度入口；本次曾据此误判"滑块下发错值"，排查后确认代码正确。

## [1.7.1] - 2026-11-03

### 变更（用户反馈："动鼠标不会亮屏吗" → 检索取证后定案）

- **DDC 熄灭：能力串闸门 + 显式风险确认**。本机面板声明 `D6(01 04 05)`，
  **不含 `02`（待机）**；而 v1.7.0 事故正是盲写 02。现在：
  - 默认跳过未声明的值，并在错误里说明面板只支持哪些值；
  - 用户在下拉里主动选「DDC 单屏待机」时，弹风险确认；确认后才置
    `ddc_allow_undeclared=True`，允许强制写入。
  依据：MS Learn `SetVCPFeature` 明确警告盲写未实现的 MCCS 值会导致
  "undefined monitor behavior"；ddcutil 文档说明 I2C 只确认"包收到了"、不确认执行。
- **唤醒序列新增"输出重驱动"一级**：`SC_MONITORPOWER 2 → -1`，强制 GPU 与面板重新握手。
  依据：公开排查一致把"亮着却黑屏"归因为 GPU-面板握手失败，标准恢复手段就是
  切一次显示模式 / 让输出重新上电。此级只在屏幕本来就黑时执行，不产生额外闪烁。
- 熄灭时同时锁屏的方案已否决（用户不要；且锁屏会打断远程会话）。

### 新增（用户反馈："检测亮灭看不出结果"）

### 新增（用户反馈："检测亮灭看不出结果"）

- **「检测亮灭」改成"一句结论 + 一行依据"**：
  - 结论行（正常字色）：`屏幕：亮着（11-03 15:11 点亮）` / `屏幕：熄灭中（… 熄灭）`；
    不再把 `0=亮（0xD6=1 开）· 1=亮（…）` 这种原始回读堆给人看。
  - 依据行（灰字，自动换行）：`依据：记账为主 · DDC 回读 0=开、1=开（会撒谎，仅参考） · 上次输入 3 分钟前`
  - 状态栏同时回声一句「检测完成 — 屏幕：…」，点完必须看得见有结果。
- **新增真正能判"亮没亮"的判据**：系统级熄灭后，只要出现过键鼠输入，Windows 就已经把
  屏幕唤醒 —— 用「熄灭时刻的 `GetTickCount`」与「当前 `GetLastInputInfo` idle」比对即可确定，
  不需要猜。判据优先级：**输入唤醒 > 软件记账 > DDC 回读**。
- 结论为"被输入唤醒"时会**顺手把记账纠正为亮**（否则界面一直显示熄灭中，而用户明明看着亮屏）。
- `--probe` / `--screen status` 输出同一套结论。

### 修复（现场事故驱动，优先级最高）

- **「熄灭」改为 Windows 系统级 `SC_MONITORPOWER`，不再用 DDC `0xD6=2`**
  - 事故记录：v1.7.0 在本机（Legion R27qe ×2，DP 直连）实测写入 `0xD6=2` 后，
    面板背光真的灭了，但**回读仍是 `1`、亮度照样读得到**；
    随后 `0xD6=1` → 写回亮度 → `0x60` 切源（4 秒往返 + 补发）全部下发成功，
    **屏幕依然是黑的**，最后靠物理电源键才恢复。
  - 结论：DDC 待机这条路"熄灭很容易、点亮不由软件决定"，不能作为默认行为。
  - 现在：熄灭 = `SendMessage(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)`，
    点亮 = `…, -1` + 一次输入注入。恢复由系统负责，不依赖面板固件。
  - 代价：只能**整组熄灭**，不能只灭其中几台（防窥场景没有区别）。
- 新增配置 `screen_off_method`：`system`（默认）/ `ddc`（高级，保留逐台 DDC 待机）。
  界面上是一个「熄灭方式」下拉框，DDC 项旁标注"本机实测可能唤不醒"。
- 定时档位的「同时熄屏 / 点亮」也走同一分派，因此定时熄屏同样可恢复
- 计划任务里的 `--apply-schedule`、CLI `--screen off|on` 一致

### 修复（走查发现）

- **枚举句柄泄漏**：`resolve_monitors()` / `selection_payload()` 每次调用都会
  `enumerate_targets()` 却不 `release_targets()` —— 保存设置、解析选择时每次漏一个句柄；
  长跑会慢慢耗尽（正是"越用越不灵"的来源之一）。现已加 try/finally
- 移除死代码 `list_monitors()`（返回未释放的句柄，纯陷阱）

## [1.7.0] - 2026-11-03

### 新增

- **三个快捷预设亮度按钮**（用户需求：一键切到常用亮度，不想拖滑块）：
  「快捷预设 `0% / 75% / 100%`」一行，点一下把**全部**显示器切到该亮度；
  值存在配置里（点「改值」改，深色模态框），按钮文字始终显示当前值。
  预设故意**不受勾选影响** —— 预设的语义是"整体到位"，少调一台会留下亮度不一致的屏
- **亮灭状态可读**（用户需求：能不能读出显示器现在亮没亮）：
  新增 `probe_states()` + 界面「检测亮灭」按钮，综合 `0xD6` 电源模式回读与
  `0x10` 亮度可达性，给出 `亮 / 灭 / 未知` **并标注依据**；
  熄屏/点亮后自动重测一次。`--probe` 等价命令行
- **`--diag` 只读诊断报告**：枚举结果（设备名/分辨率/主副屏/物理描述）、
  每个输出的 `0xD6 / 0x60 / 0x10` 回读、能力串解析出的型号与支持输入源、
  设备名锚点解析结果、三条例行任务状态。专为"另一台电脑点不亮、现场无法调试"设计
- `--list` 增加当前输入源（`DP-1` / `HDMI-1` …）
- 点亮时是否允许「切信号源」做成一开关（`wake_switch_input`，默认开）

### 修复

- **点亮不可靠（换电脑后有时点不亮，尤其 DP+HDMI 双线的屏）**，三层原因都改掉了：
  1. 旧版**唯一**的唤醒手段是 `0x60` 切信号源再切回，且固定的"另一个输入"写死成
     `4`（DVI-2）—— 多数显示器根本没有这个口，切过去即无信号、面板进省电，
     切回来能不能醒取决于链路重训练，"有时行有时不行"就是这么来的。
     现在改为**分级唤醒**：`0xD6=1` → 写回亮度 → 系统输入注入（`mouse_event` 抖动
     1px，踢醒 Windows 的显示状态）→ 最后才切信号源，且只在能力串声明的口里选、
     只选 DP/HDMI、优先"另一种口"
  2. **序号漂移**：`monitors` 只存下标，而 `EnumDisplayMonitors` 的顺序会随驱动/接线
     重排 → 熄的是 A、开机后点的是 B。现在配置同时存 `monitor_ids`（设备名锚点），
     执行前先按设备名重算下标
  3. **已经亮着的屏也被切信号源**（白闪 2~3 秒，双线时还可能切到空口）：
     现在只有"软件确实熄过这块屏"时才做这一招，亮着的时候只发电源/亮度/输入三级
- **`0xD6` 回读在待机状态下也会撒谎**（现场取证）：写入 `0xD6=2` 之后回读仍是 `1`、
  亮度照样读得到。所以**不拿回读判断"是否点亮成功"**——只保证命令下发到位，
  界面把回读值作为"证据"显示，并提示"若确实黑着该怎么办"（否则会出现
  "程序说点亮成功、屏幕其实黑着"，与 v1.5.0 事故同源）
- **`monitorcontrol` 4.2.0 在 Python 3.14 上枚举直接抛 `TypeError`**
  （`expected LP_PHYSICAL_MONITOR instance`，新版 ctypes 对"数组冒充指针"更严格），
  源码态根本列不出显示器。改为**自带 dxva2 后端**，`monitorcontrol` 不再是依赖
- **两套枚举各排一次序**：`monitorcontrol` 与 `enum_displays()` 各自枚举，
  顺序一旦不同就整体错位。现在只有 `enumerate_targets()` 一个枚举源
- **DDC 句柄泄漏**：旧代码只 `GetPhysicalMonitorsFromHMONITOR` 不
  `DestroyPhysicalMonitor`，反复操作会慢慢耗尽句柄（长跑后"越用越不灵"）
- **能力串数值是十六进制**（本机实测 `60(11 12 0F)` = HDMI-1/HDMI-2/DP-1）：
  按十进制解析会得到 `11/12/0` 这种不存在的口，拿去切信号源等于把画面切进黑洞
- **窗口高度超过 1080p 屏幕**（5 个时间段时 1117px，底部控件点不到）：
  内容区改为可滚动，高度按屏幕可用高度封顶，宽度仍随内容自适应

### 变更

- 依赖：`monitorcontrol` 从"必需"改为"不需要"；托盘仍需 `pystray` + `pillow`（可选）
- 配置文件版本 `4 → 5`（新增 `presets` / `monitor_ids` / `wake_switch_input`，旧配置自动兼容）

## [1.6.5] - 2026-10-08

### 新增（用户需求：屏幕是显示还是熄灭，能读出并显示）

- **屏幕状态记账与显示**：软件每次执行熄灭/点亮（按钮 / 托盘 / 热键 / CLI / 定时档）
  成功后都把状态记入配置并落盘；界面「熄屏 / 点亮 / 托盘」区、托盘悬停、
  `--screen status` 都会显示当前状态（熄灭中 / 点亮中）
- ⚠️ **如实标注数据来源**：本机 DDC `0xD6` 硬件回读不可信（寄存器值 ≠ 面板事实，
  v1.5.0 熄屏事故实测），故显示的是「软件最后一次操作记录」——
  物理电源键 / 系统自动熄屏不在此追踪，界面已注明
- 自测 139 → 144 项（记账 / 显式 cfg 不落盘由调用方管 / 无动作档不改状态）

## [1.6.4] - 2026-10-08

### 修复

- **「屏幕」下拉框回显方向反了**（用户反馈：重启后设置的熄灭/点亮全变回「不变」）：
  存储一直是对的（config 里 screen=off/on 都在），但重开程序构建行时，
  用「标签→值」字典去查「值」—— 永远查不到而回落「不变」；
  此时再点保存就会把「不变」写回去（破坏性）。改为正确的「值→标签」映射
  `SCREEN_LABEL_BY_VALUE`，并新增往返测试（config → 下拉框显示 → 读回不丢）

## [1.6.3] - 2026-10-08

### 新增

- **任务计划状态展示真实注册的定时清单**（用户需求：能看清现在已有的定时）：
  「定时」行展开为系统里**实际注册**的每个触发时间，并与当前配置联显
  亮度 / 屏幕动作 —— 如 `• 21:00 → 0%`、`• 23:00 → 0%（同时熄屏）`；
  时间在任务里注册了但配置中已删的档，会标注「当前配置中无此档」，
  方便发现配置与注册不同步；数据来自任务 XML（快路径，无 TRIG 的兜底路径回退单行）
- 窗口高度随触发清单行数自适应
- 自测 131 → 137 项（清单渲染 / 兜底回退 / 不同步标注；单实例互斥体名可注入，
  测试不再受"用户正开着 GUI"影响）

## [1.6.2] - 2026-10-08

### 新增

- **托盘开关**：界面新增「系统托盘图标」勾选框（默认开）——关闭后不创建托盘、
  点 X 直接退出程序；随时可再打开（运行中即时生效）
- **单实例限制**（用户反馈：多次打开会开多个）：GUI 启动时获取命名互斥体，
  重复启动时唤起已有实例的主窗口（含托盘隐藏态）然后退出；
  ⚠️ **只约束 GUI** —— CLI 模式（计划任务的 --apply-schedule 等）不获取互斥体，
  避免托盘常驻时定时任务被自己拦死
- 自测 126 → 131 项（tray_enabled coerce / 单实例首次获取与二次拒绝实测）

## [1.6.1] - 2026-10-08

### 修复

- **「屏幕」下拉框深色模式**（用户反馈）：ttk Combobox 输入框部分走 `TCombobox` style
  （fieldbackground/arrowcolor 均深色）；弹出的下拉列表是原生 Listbox 且弹层路径
  可能不含 TCombobox 前缀，故 `*TCombobox*Listbox.*` 与 `*Listbox.*` 两种
  option 模式双保险，深色底/浅色字/主题蓝选中

## [1.6.0] - 2026-10-08

### 新增（S7：定时熄屏 + 托盘 + 开机启动）

- **定时熄屏/点亮**（调度模型 v4）：每个时间段新增「屏幕」下拉框（不变/熄灭/点亮），
  到点由任务计划执行 `--apply-schedule` 时同时下发亮度 + 屏幕动作 —— 依旧零常驻；
  旧配置自动迁移（缺 screen 字段补「不变」）
- **系统托盘**（pystray，独立 daemon 线程；回调经 `root.after(0,…)` 回主线程）：
  悬停显示定时摘要（各档位 + 下次运行时间）；左键打开主窗口；
  右键菜单：打开 / 熄灭 / 点亮 / 启用定时（勾选态实时反映）/ 退出
- **点 X = 最小化到托盘**（退出在托盘菜单）；缺 pystray 环境自动降级为直接退出
- **开机自动启动**：HKCU Run 注册表键（免管理员），界面勾选即生效，
  启动后直接进托盘（`--minimized`）
- 托盘菜单可直接切换「启用定时」—— 勾上=写任务计划，取消=注销任务，与主窗口同步
- 自测 113 → 126 项（v4 迁移 / screen 字段 coerce / active_slot_dict 含跨午夜回绕 /
  apply_schedule 亮度+屏幕动作联动；另修测试自身两处缺陷）

## [1.5.0] - 2026-10-08

### 新增（S6：熄屏 / 点亮 —— 远程防窥场景）

- **熄灭显示器**：DDC `0xD6=2`（待机）—— 面板全黑，Windows/远程输入都唤不醒它
- **点亮显示器**：**信号源自动切换**（`0x60` 换输入口 3 秒再切回）—— 本机单变量实测
  唯一可靠的软件唤醒路径（端到端 2/2 成功）；点亮时亮着的屏会闪 3 秒（正常现象）
- **命令行**：`--screen off|on`（远程时敲命令即可，零常驻）
- **可选热键**（默认关）：Ctrl+Alt+Shift+O 熄灭 / P 点亮；开启需保持程序运行（常驻监听）
- **保险丝**：熄灭后 N 分钟自动点亮（可设 0-120 分钟，默认关；远程建议设值防失联）
- ⚠️ **硬件红线（实测事故换来的）**：`0xD6=4/5`（软关/硬关）在本机 Legion R27qe 上
  面板固件级唤不醒 —— DDC 点亮/驱动重启/拓扑重握手/信号源切换全部无效，
  只有物理电源键能救，且 0xD6 回读值是假的。**代码只允许用待机档(2)**，红线写进源码注释
- 自测 104 → 113 项（含 AST"未定义名"守卫抓到一处真 bug：`import ctypes.wintypes`
  不绑定裸名 wintypes，服务层运行时会 NameError —— 模块级显式导入修复）

## [1.4.1] - 2026-10-08

### 变更（仓库信息维护；程序行为与 1.4.0 完全一致）

- 新增 **LICENSE**（MIT）
- 新增 **.gitattributes**（行尾规范：仓库 LF，`*.bat`/`*.cmd` 强制 CRLF —— cmd 对 LF 的
  goto/标签解析不可靠；图片与 exe 标记 binary）
- README 顶部加 Release / License / Platform 徽章
- GitHub 仓库元信息：补 topics、homepage（指向 releases/latest）、description 更新为多时间段口径

## [1.4.0] - 2026-10-08

### 新增（深色弹窗）

- **所有弹出通知改为深色模式**（用户反馈：启用定时后弹出的通知不是深色）：
  自定义 `DarkBox` 模态对话框全面替代 `messagebox` —— 深色背景/文字、主题蓝"确定"按钮、
  居中于主窗口、支持 Enter/ESC 关闭，info/warn/error 三种类型各配图标与颜色
- 原因：messagebox 是 Windows 原生对话框，不吃 ttk 深色主题；
  实测 `SetPreferredAppMode(ForceDark)` 只能暗标题栏、内容区仍为浅色（A/B 截图：
  标题栏暗但内容均值 184 vs 基线 196），故放弃该未公开 API 路线，改用完全可控的自绘对话框
- 模态模式与 CustomTkinter 对话框一致（Toplevel + grab_set + wait_window）
- 移除无效果的 `_force_dark_dialogs`（未公开序数 API，避免系统更新后失效的隐患）

## [1.3.2] - 2026-10-08

### 修复

- **任务计划状态显示「读取失败」**（用户实机抓到）：S5 新增的 `_next_run_text` 里
  `(now or datetime.now()).hour * 60 + now.minute` —— `.minute` 读的还是原始 `now`（None）
  → AttributeError；而 `query_task_status_local` 的解析段没有 try 保护，异常炸出去
  被 job 兜底接住 → 返回空串 → 两条任务都显示「读取失败」。
  现改为 `now_dt = now if now is not None else datetime.now()`（与 `_minutes` 同款写法），
  并补了真实路径测试（query_task_status 本机实测返回 Schedule/CatchUp 两行）。
  此前 96 项自测没抓到的原因：任务状态查询这条真实路径从未被任何测试调用过。

## [1.3.1] - 2026-10-08

### 修复

- **点「保存并启用定时」报 NameError: register_tasks is not defined**（用户实机抓到）：
  S5 施工按锚点整段替换时，把夹在 `build_register_script` 与 `build_unregister_script`
  之间的 `register_tasks` 一并删掉了；96 项自测当时没抓到 —— 因为没有任何测试调用它。
  现已恢复，并加了两道守卫：
  1. **模块完整性检查**（新增自测）：迷你"未定义名"分析 —— 全模块每个函数体内引用的、
     既非局部又非模块级/builtin 的名字必须为 0（含反向对照：在内存里删掉
     register_tasks 后检查器必须报出 `App._save_schedule_inner -> register_tasks`）
  2. `register_tasks` / `unregister_tasks` 存在性断言

## [1.3.0] - 2026-10-08

### 新增（S5：多时间段）

- **时间段数量不再固定为两档**：定时区改为动态行，点「+ 添加时间段」随时追加
  （时间 + 亮度），每行带「删除」按钮（至少保留一档）
- **调度语义（N 档）**：按时间排序，取「time <= 当前时刻」的最后一档生效；
   早于全部档位时取最后一档（跨午夜回绕）。同一时刻只能有一档
- **计划任务合并**：Day/Night 两条任务合并为一条 `MonitorBrightness_Schedule`，
  挂 N 个 Daily 触发器（一条任务多触发器是任务计划程序标准能力）；
  旧任务名注册时自动清理；CatchUp 登录校准任务保留
- **配置 v3**：`slots` 列表取代 day_time/day_level/night_time/night_level 四键，
  旧配置自动迁移（v1/v2 → v3 不丢设置）；「测试当前档」按钮按当前时刻判定下发
- 实测：96 项自测（含 N 档回绕/单档/三档/乱序/迁移）+ 表单交互 9 项 + 下发 4 项全过

## [1.2.3] - 2026-10-08

### 变更（S3 视图/协调者分离，行为不变）

- `App`（517 行 / 34 方法）拆为三个 `ttk.Frame` 子类 + 瘦身后的协调者：
  - `MonitorPanel`（64 行 / 5 方法）：勾选行、全选/全不选、就地改文字
  - `BrightnessPanel`（44 行 / 5 方法）：滑块、"当前亮度"行、应用/读取按钮
  - `SchedulePanel`（101 行 / 9 方法）：启用开关、两档表单与校验、测试/保存/取消、任务状态
  - `App`（**304 行 / 24 方法**，-41%）只保留服务调用、异步编排与弹窗，不再出现建控件细节
- 实测：91 项自测 + 勾选/读取功能 5/5 通过；CC>10 为 0；6 处耗时调用全部后台化
- 顺带：`load_config` 拆出 `_load_model_cache`（CC 11 → 7）
## [1.2.2] - 2026-10-08

### 变更

- **计划任务改指 v1.2.x 的 exe**（此前指向 v1.1.0.exe，工作区一旦清理任务会静默失效）：
  用应用自己的 `build_register_script` 重新注册三条任务（用户真实配置不变），
  实测 `CATCHUP=OK / StartWhenAvailable=True / CatchUpTask=Ready / REGISTER_OK`，
  三个任务 XML 的 Command 均指向 v1.2.x exe
- 文档：AGENTS 基线更新到 v1.2.x、REFACTOR_PLAN 标记 S1/S2/S4 完成（S3 待做）
- 代码行为与 1.2.1 一致，本次升号对应上述文档与任务指向变化
- **规则固化**：今后任何修改（含文档）→ 版本号 +1 → 重新打包 → 重注册任务 → 交付

## [1.2.1] - 2026-10-08

### 变更

- **型号名读取提速**：实测读亮度只要 **~127 ms**，慢的是型号名 —— DDC 能力串 **~3.1 s/台**，
  原先串行读 2 台要 **6.2 s**。改为：
  1. 每台一个线程**并行**读（2 台 ~3.1 s）；
  2. 型号名**持久缓存**进 config.json 的 `models` 字典（按显示器序号），
     **第二次启动起 0 ms 秒出**，DDC 只在缓存缺失时才读
     （换插口导致序号变化时会重新读一次再缓存）。

## [1.2.0] - 2026-10-08

### 变更（架构重构，行为不变）

- **S1 统一异步入口** `run_bg(job, on_done, busy=False)`：此前 6 个耗时动作各自手写
  「建线程 + 投队列 + 写 `_handle_*` + 注册分发表」；现在只剩一个入口，
  新增异步动作只需写一个回调。job 抛出的异常也会回主线程交给回调，不再后台静默吞掉。
- **S2 设置单一来源**：新增 `SETTINGS` 声明表（key/kind/default）与 `SCHEDULE_ROWS`
  （时间+亮度+任务名+标题 绑成一行），默认配置、逐键校验 `coerce_setting`、表单变量、
  两行控件、测试按钮、读表单、注册脚本参数、档位判定全部由表派生。
  实测触点：`day_time`/`day_level`/`night_time`/`night_level` 从 8~9 处降到 **2 处**；
  新增一档只需 `SCHEDULE_ROWS` 加一行 + `SETTINGS` 加两个键。
- 性能保持：`App(root)` 构造+首帧 **~70 ms**（重构前 2639 ms）；6 处耗时调用全部后台化。
- **型号名读取提速**（用户反馈"亮度读取感觉也慢"）：实测读亮度只要 **~127 ms**，
  慢的是型号名 —— DDC 能力串 **~3.1 s/台**，原先串行读 2 台要 **6.2 s**。
  改为①每台一个线程**并行**读（2 台 ~3.1 s）；②型号名**持久缓存**进 config.json，
  第二次启动起 **0 ms** 秒出，DDC 只在缓存缺失时才读（按显示器序号缓存，
  换插口导致序号变化时会重新读一次再缓存）。

### 已知

- `App` 类 517 行 / 34 方法，仍超 Fowler 大类阈值；下一阶段（S3）把三个分区抽成
  `ttk.Frame` 子类，计划见 `REFACTOR_PLAN.md`。

## [1.1.0] - 2026-10-08

### 新增

- **深色主题**：ttk `clam` 主题自定义配色 + DWM 沉浸式深色标题栏，界面无白色区域残留
- **显示器勾选列表**：**每台显示器前面一个勾选框**（勾选态清晰、支持禁用态），
  勾上哪几台就一起下发同一亮度，不用再按 Ctrl/Shift 做多选；
  保留「刷新 / 全选 / 全不选」按钮
- 显示器条目显示**型号 · 分辨率 · 主/副屏 · 设备名**（如 `Legion R27qe Gen2 · 2560×1440 · 主显示器 · DISPLAY1`），
  同型号屏幕也能区分
- 「当前亮度」异步读取，界面不卡死；新增「读取当前亮度」按钮
- 新增 CLI：`--apply-schedule`（按当前时间判定档位下发）、`--list`、`--selftest`

### 修复

- **界面底部「任务计划状态」一直显示「读取失败」**：`query_task_status` 用 `'; '.join`
  拼接 PowerShell 语句，把分号插到了 `}` 和 `else` 之间 →
  PowerShell **语法错误**（实测报「无法将"else"项识别为 cmdlet」）→ 整段解析失败、
  退出码非 0 → 界面只能显示「读取失败」。
  现统一改用换行拼接的 `_ps_script()`（register / unregister / query 三处），
  并只认三个已知任务名，避免 PowerShell 的错误文本混进状态列表被渲成假条目。
- **「登录后自动校准」任务注册不出来**（实机抓到的 bug）：`New-ScheduledTaskTrigger -AtLogOn`
  不带 `-User` 会被判成"任意用户登录"触发，非管理员注册直接报「拒绝访问」；
  又因为脚本带 `$ErrorActionPreference='Stop'`，整段在注册 logon 任务那一步中断，
  白/黑两条日任务之后的所有语句（含 `StartWhenAvailable` 回读）都没执行。
  现改为 `-AtLogOn -User $me`，并把这条单独包 `try/catch`：失败也不拖垮其余任务，
  原因通过 `CATCHUP=FAIL` 带回界面提示。
- **点「保存并启用定时」无声无息**：`save_schedule` 里引用了不存在的属性
  `self.catch_up_on_logon`（正确是 `self.catchup_var`），抛 `AttributeError`；
  而 `--windowed` 打包没有控制台，异常直接消失、连对话框都不弹。
  现整个回调包了异常兜底 —— 任何意外都会变成对话框，不再"点了没反应"。
- **定时在开机时间已过时不生效**（核心修复）：`schtasks.exe` 命令行**没有**「错过就尽快补跑」开关，
  v1.0.0 用它注册的任务在电脑关机 / 睡眠错过时间点后不会补跑。
  改为 PowerShell `Register-ScheduledTask` + `New-ScheduledTaskSettingsSet -StartWhenAvailable`。
- **两条任务同时被补跑会互相覆盖**：任务动作统一改为 `--apply-schedule`，
  由程序按**执行时刻**自行判定该用白天还是晚上档（带 ±2 分钟抖动容错），因此结果幂等。
- **新增「登录后自动校准」任务**（`MonitorBrightness_CatchUp`，登录后 30 秒执行），
  保证开机时间已晚于切换点时也能立刻补上，不依赖 StartWhenAvailable 单独生效。
- 源码模式下注册的任务跑不起来：v1.0.0 用 `sys.executable` 当程序路径，
  直接跑脚本时那是 `python.exe` 而非目标程序。现改为打包态用自身 exe、源码态带上脚本路径。
- 界面卡在「读取中…」：后台线程里调 `root.after` 会抛 `main thread is not in main loop`。
  改为「后台线程 → 队列 → 主线程轮询」，全程不跨线程碰 Tk 控件。
- 「当前亮度」迟迟不出：型号名（MCCS 能力串）实测约 3 秒/台，与亮度读取解耦成两个后台任务。
- 显示器名称显示成 `<Monitor object at 0x...>`：`monitorcontrol` 的 Monitor **没有** `model` 属性，
  旧写法从未生效。现从 MCCS 能力串取型号，并配合 Win32 枚举补分辨率与主/副屏。

### 变更

- **性能：把所有耗时操作移出主线程**（用户反馈"卡顿感太强"）。修复后「双击到看见窗口」
  从 **2639 ms 降到 70 ms**：
  | 操作 | 修复前（主线程同步） | 修复后 |
  |---|---|---|
  | 启动时查任务计划状态 | 2441 ms（PowerShell 冷启动） | **0 ms**（改读本地任务 XML，~1 ms） |
  | 点「保存 / 取消定时」 | ~3000 ms 界面冻结 | 后台执行，不冻结 |
  | 点「应用到选中显示器 / 测试」 | 370 ms 界面冻结 | 后台执行，不冻结 |
  - 任务状态改走 `query_task_status_local()`：直接解析 `<SystemRoot>\System32\Tasks\<任务名>`
    的 XML（UTF-16 LE），取 Enabled / StartWhenAvailable / StartBoundary，
    「下次运行」由 StartBoundary 的本地时间推算（显示为「今天/明天 HH:MM」）；读不到才回退 PowerShell。
  - 状态区**固定 4 行**（启动即写占位），消除异步结果回来时的窗口高度跳变。
  - 型号名到达时**就地改那一行文字**，不再销毁重建整个勾选列表。
  - 耗时操作进行中禁用相关按钮，避免连点起一串后台线程。
- **结构：按量化审计结果重构**（判据见 DEVELOPMENT.md「架构健康度」）
  - `App._build` 92 行 → 拆成 `_build_monitor_group` / `_build_brightness_group` /
    `_build_schedule_group`（最长 35 行）
  - 白天/晚上两行输入合并为 `_build_time_row()`，去掉一份重复
  - 圈复杂度 >10 的方法：**4 个 → 0 个**
- **显示器改为每台一个勾选框**：勾上哪几台就一起下发同一亮度，不用再按 Ctrl/Shift 多选；
  保留「刷新 / 全选 / 全不选」
- 配置文件升级到 v2：`monitor`（单序号）→ `monitors`（序号列表）；旧配置自动迁移
- 定时档位校验：白天与晚上时间不得相同，时间格式必须是 `HH:MM`
- **界面配色重做**：去掉满屏饱和蓝，改成中性灰打底（`#1e1e1e` / `#252525` / `#333`），
  强调色只出现在「主按钮 / 勾选框选中态 / 列表选中」三处；分组标题由"蓝色粗体"改为常规字重浅灰；
  滑块改为中性灰
- **勾选框改为 ttk 官方扩展方式**：用 `style.element_create` + `style.layout`
  替换 `Checkbutton.indicator` 指示器元素（纯代码生成的图片，不引入外部资源文件）。
  ⚠️ 修正上一版的做法：原先给 `tk.Checkbutton` 设 `selectcolor=强调色`，
  导致**未勾选时方块也是蓝色的**，跟勾上分不出来（用户反馈的问题）

## [1.0.0] - 2026-09-25

### 新增

- 图形界面（tkinter）：显示器选择、亮度滑块即时调节
- 每日定时：白天 / 晚上两个时间点与亮度，一键写入 Windows 任务计划程序
- 命令行模式：`--set <index> <value>`，供任务计划静默调用
- 单文件 exe 打包（PyInstaller `--onefile --windowed`，约 14 MB）
- 配置持久化：`%APPDATA%/MonitorBrightness/config.json`
- 附 PySide6/Qt 界面变体源码（`monitor_brightness_qt.py`）

[1.0.0]: https://github.com/Simiely/MonitorBrightness/releases/tag/v1.0.0
