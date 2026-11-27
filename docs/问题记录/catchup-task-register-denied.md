# 问题：「登录后自动校准」任务注册不出来 + 点保存无声无息

> 实机抓到的复合 bug：一个在注册脚本里，一个在 UI 回调里。

**TL;DR**：`-AtLogOn` 必须带 `-User`，否则注册被拒（访问被拒绝）；而且 `--windowed`
没有控制台，回调里的异常会**静默消失**，用户看到的就是"点了没反应"。

- **怎么发现的**：用户在本机跑过新版并点了保存，但任务只多出 Day / Night。
  读 `C:\Windows\System32\Tasks\` 下的任务 XML（**UTF-16 LE 编码**，直接 grep 匹配不到）
  确认 `MonitorBrightness_CatchUp` 不存在 —— 而配置里 `catch_up_on_logon` 是 `true`。
- **根因 1（任务缺失）**：`New-ScheduledTaskTrigger -AtLogOn` 不带 `-User` 时，
  触发器的 `UserId` 为空 → 被当作"**任意用户登录**"→ 非管理员注册直接
  **「拒绝访问」**。实测对照：

  | 写法 | 非管理员下结果 |
  |---|---|
  | `New-ScheduledTaskTrigger -AtLogOn` | ❌ 拒绝访问 |
  | `New-ScheduledTaskTrigger -AtLogOn -User <当前用户>` | ✅ 成功 |

  又因为脚本开头是 `$ErrorActionPreference = 'Stop'` 且用 `;` 串联，
  这一句失败会让**后面所有语句都不执行** —— 包括最后那句
  `StartWhenAvailable` 回读，所以界面里连"补跑已开/未开"都读不到。
  → 改成 `-AtLogOn -User $me`（`$me = $env:USERNAME`，并留了 `WindowsIdentity` 兜底），
  且把这条**单独包 `try/catch`**：失败只输出 `CATCHUP=FAIL <原因>`，不拖垮白/黑两条。
- **根因 2（点保存没反应）**：`save_schedule` 里写了 `self.catch_up_on_logon`，
  这个属性**根本不存在**（正确的是 `self.catchup_var`）→ `AttributeError`。
  打包成 `--windowed` 后没有控制台，Tk 回调里的异常没有任何出口，用户视角就是"点了没反应"。
  → 拆成 `save_schedule()`（只做异常兜底）+ `_save_schedule_inner()`（真正逻辑），
  任何意外都变成弹窗。
- **预防（已固化）**：
  1. 自测里加了针对 `-AtLogOn -User` 的**正向断言 + 反向对照**；
  2. 脚本改为会逐条回读三个任务（`CatchUpTask=MISSING/Ready`）再输出 `REGISTER_OK`；
  3. **经验**：`--windowed` 程序里任何用户可触发的回调都必须包异常兜底，
     否则 bug 会以"点了没反应"的形式完全隐身。
- **验证方式（没用管理员、也没动用户的任务）**：把生成的脚本里三个任务名替换成
  `MBTEST_*` 实跑一遍 → 输出 `CATCHUP=OK` / `StartWhenAvailable=True` /
  `CatchUpTask=Ready` / `REGISTER_OK`，三个 XML 都真的落盘；
  再解出 `MBTEST_CatchUp` 的 XML 确认 `LogonTrigger` 里有 `<UserId>` 与 `<Delay>PT30S</Delay>`；
  最后删掉测试任务并复查用户原任务未受影响。
