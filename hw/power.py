# -*- coding: utf-8 -*-
"""开关屏与点亮原语（v2.03.0 R2，从 monitor_brightness.py 逐字搬出）。

系统级 BroadcastSystemMessage、DDC 待机、切源/快速翻转等纯动作；
按配置分发的策略在 service.py。
"""
import ctypes
import threading
import time
from ctypes import wintypes

from core.config import normalize_indices
from hw.ddc import (
    NO_MONITOR_MSG, POWER_ON, POWER_STANDBY, VCP_BRIGHTNESS,
    VCP_INPUT_SOURCE, VCP_POWER_MODE, _capabilities_cached, _caps_inputs,
    _caps_power, _display_key, _read_vcp, _select, _write_vcp,
    enumerate_targets, release_targets,
)

# ---- 熄屏 / 点亮 ----------------------------------------------------------
# ⚠️ 硬件红线，两条，都有本机实测事故背书：
# 1) **绝不用 `0xD6=4/5`（软关/硬关）** —— 面板进固件级熄灭，DDC 点亮 / 驱动重启 /
#    拓扑重握手 / 信号源切换全部唤不醒，只有物理电源键能救。
# 2) **v1.7.0 现场事故：`0xD6=2`（待机）也不该用来熄灭** ——
#    本机写 `0xD6=2` 后：回读仍是 `1`、亮度照样读得到，但面板背光真的灭了；
#    随后 `0xD6=1`、写亮度、`0x60` 切源（4 秒往返 + 补发）全部救不回来，
#    最后靠物理电源键恢复。结论：**"熄灭"容易，"点亮"却不由软件决定**。
#
# 所以 v1.7.1 起，熄灭改走 Windows 系统级：
#     SendMessage(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
#   - 恢复由系统负责（`SC_MONITORPOWER -1` 一定亮回来），不依赖面板固件；
#   - 代价是只能整组熄灭，不能只灭其中几台 —— 对防窥场景没有区别。
#   DDC 待机方式保留为高级选项（`screen_off_method=ddc`），默认关闭。
SC_MONITORPOWER = 0xF170

SC_OFF = 2          # 关闭显示器（比 1 低功耗更彻底，系统仍能恢复）

SC_ON = -1          # 点亮显示器

_WM_SYSCOMMAND = 0x0112

_HWND_BROADCAST = 0xFFFF

# 唤醒分级：DDC 方式下的顺序，从"最标准、最不可能把屏搞黑"的一招开始。
# 为什么不再一上来就切信号源（v1.6.x 的做法）：
#   旧代码把"切到另一个输入"写死成 4（DVI-2）。现代显示器基本没有 DVI-2，
#   切过去等于切到无信号口 → 面板进省电，再切回来能不能醒取决于链路重训练，
#   于是表现为"有时行、有时不行"。若这台屏还同时插了 DP+HDMI 两根线，
#   切到 HDMI 正好切到 Windows 没在驱动的那个空口上，画面直接黑在空口。
WAKE_STAGE_LABEL = {1: "电源模式", 2: "写亮度", 3: "系统输入", 4: "切信号源"}

def _broadcast_monitor_power(flag):
    """向系统广播"显示器电源"命令，返回是否下发成功。"""
    try:
        return bool(ctypes.windll.user32.SendMessageTimeoutW(
            _HWND_BROADCAST, _WM_SYSCOMMAND, SC_MONITORPOWER, flag, 0, 2000, None))
    except Exception:
        return False

def screen_off_system():
    """系统级熄灭全部显示器（可恢复）。"""
    return _broadcast_monitor_power(SC_OFF)

def screen_on_system():
    """系统级点亮，并补一次输入事件（Windows 有时不主动重发画面）。"""
    ok = _broadcast_monitor_power(SC_ON)
    _nudge_user_input()
    return ok

def _nudge_user_input():
    """喂一次真实的用户输入事件。

    Windows 有时在收到点亮命令后仍不重发画面（它仍认为显示器处于关闭状态），
    一次微小的鼠标移动就能把它踢回"有显示"。移 1 像素再移回来，肉眼无感。
    """
    try:
        u = ctypes.windll.user32
        u.mouse_event(0x0001, 1, 0, 0, 0)      # MOUSEEVENTF_MOVE
        u.mouse_event(0x0001, -1, 0, 0, 0)
        return True
    except Exception:
        return False

def _switch_input_roundtrip(t):
    """切到"另一个真实支持的输入源"，等 2 秒再切回来。返回两端是否都下发成功。

    选源规则（顺序即优先级）：
      1) 能力串里 0x60 声明支持的口（真实存在的口）；
      2) 其中只留 DP/HDMI（15~19）—— 模拟/DVI/复合口切过去基本等于无信号；
      3) 优先挑"与当前口异类"的：当前在 DP 就先试 HDMI，反之亦然，
         因为双线接法下另一根线通常是另一种口，切过去有极大概率真的有信号。
    **不再用旧版的 4（DVI-2）**：那是多数显示器根本没接的口，切过去即黑屏。
    """
    h = t["handle"]
    got = _read_vcp(h, VCP_INPUT_SOURCE)
    if not got:
        return False
    cur = got[0]
    declared = [c for c in _caps_inputs(_capabilities_cached(h, _display_key(t)))
                if 15 <= c <= 19]
    cand = [c for c in (declared or [15, 16, 17, 18, 19]) if c != cur]
    if not cand:
        return False
    cur_is_dp = 15 <= cur <= 16
    cand.sort(key=lambda c: (cur_is_dp == (15 <= c <= 16), c))
    if not _write_vcp(h, VCP_INPUT_SOURCE, cand[0]):
        return False
    time.sleep(2)
    return _write_vcp(h, VCP_INPUT_SOURCE, cur)

def _wake_one(t, allow_switch=True, expect_off=True, allow_recycle=True):
    """分级唤醒单台显示器。返回 (命令是否都下发成功, 走到第几级, 说明)。

    ⚠️ **不用 `0xD6` 回读判断"醒了没有"**（v1.7.0 现场取证）：
    本机写入 `0xD6=2` 待机之后，回读仍是 `1`、`0x10` 亮度也照样读得到。
    拿它当判据就会出现"程序说点亮成功、屏幕其实黑着"——和 v1.5.0 熄屏事故同源。
    所以这里只保证"该发的命令都发到位"，真实亮灭交给「检测亮灭」+ 眼睛。

    ⚠️ `allow_recycle=False`（v1.9.6）：只勾选了部分显示器时禁用第 3 级的
    `SC_MONITORPOWER` 广播。那是**系统级**调用，会作用于所有显示器，把没勾选的
    那台也黑一下再点亮。此时只用每台的 `0xD6=1` + 亮度写 + 输入注入。

    顺序按"危害从小到大"，前三步都不碰信号源：
      1 `0xD6=1` 电源模式=开（标准唤醒命令，无害）
      2 写回当前亮度（部分固件靠收到 VCP 写入才醒）
      3 **让系统把显示输出关一次再打开**（SC_MONITORPOWER 2→-1）+ 输入注入。
        依据：显示器"亮着却黑屏"的公开排查一致指向 GPU 与面板握手失败，
        标准恢复手段就是切一次显示模式 / 让输出重新上电。此时屏幕本来就是黑的，
        重驱动不会造成额外闪烁。
      4 切信号源（唯一可能把屏切到空口的一招：受 allow_switch 控制，
        并且只在"软件确实熄过这块屏"时才做，避免给亮着的屏白闪 2 秒）
    """
    h = t["handle"]
    wrote = False
    stage = 0

    if _write_vcp(h, VCP_POWER_MODE, POWER_ON):
        wrote, stage = True, 1
        time.sleep(0.35)

    cur = _read_vcp(h, VCP_BRIGHTNESS)
    if cur and _write_vcp(h, VCP_BRIGHTNESS, cur[0]):
        wrote, stage = True, 2
        time.sleep(0.35)

    if not wrote:
        # 两个最基本的写都不通，后面切信号源也不会通，早点收工别让用户干等
        return False, 0, "显示器未响应 DDC/CI（电源 / 亮度写入均失败）"

    output_recycled = False
    if not expect_off:
        _nudge_user_input()          # 屏幕本来就亮着：只喂输入，别去动输出
    else:
        if allow_recycle:
            output_recycled = _output_recycle()
        _nudge_user_input()
    stage = max(stage, 3)
    time.sleep(0.35)

    if not allow_switch:
        note = ("已下发电源/亮度/输出重驱动" if output_recycled
                else "已下发电源/亮度/输入注入")
        return wrote, stage, note + "（切信号源已禁用）"
    if not expect_off:
        return wrote, stage, "已下发电源/亮度/系统输入（未熄过屏，跳过切信号源）"
    if not _switch_input_roundtrip(t):
        note = "切信号源失败（前几级已下发）"
        if output_recycled:
            note += "，输出已重新驱动过"
        return wrote, stage, note
    return wrote, 4, "已下发电源/亮度/输出重驱动/信号源切换"

def _output_recycle():
    """把显示输出关一次再打开，强制 GPU 与面板重新握手。

    返回是否两次下发都成功。依据见 _wake_one 第 3 步的说明。
    """
    off_ok = _broadcast_monitor_power(SC_OFF)
    time.sleep(0.8)
    on_ok = _broadcast_monitor_power(SC_ON)
    return off_ok and on_ok

def screen_off_many(indices, allow_undeclared=False):
    """批量熄灭（DDC 待机 0xD6=2）。indices 空 -> 全部。

    ⚠️ 先查面板能力串：**没声明支持 02（待机）的一律跳过**，不做"写写看"。
    依据：MS Learn 的 SetVCPFeature 文档明确警告盲写未实现的 MCCS 值会导致
    "undefined monitor behavior"；本机面板正是不支持 02 却被盲写，
    结果进入了 DDC 能读、背光却唤不回来的状态。
    """
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    ok, errs = [], []
    try:
        picked, bad = _select(targets, indices)
        errs.extend(bad)
        for t in picked:
            declared = _caps_power(_capabilities_cached(t["handle"], _display_key(t)))
            if POWER_STANDBY not in declared and not allow_undeclared:
                errs.append((t["index"], "面板未声明支持待机(0xD6=2)%s，已跳过"
                             "（要强制启用请重选「单屏待机」并确认风险）"
                             % ("，只支持 %s" % "/".join(str(v) for v in declared)
                                if declared else "（能力串也读不到）")))
                continue
            if _write_vcp(t["handle"], VCP_POWER_MODE, POWER_STANDBY):
                ok.append(t["index"])
            else:
                errs.append((t["index"], "SetVCPFeature 失败"))
    finally:
        release_targets(targets)
    return ok, errs

def screen_wake_many(indices, allow_switch=True, expect_off=True):
    """批量点亮（分级唤醒，并行执行）。indices 空 -> 全部。

    并行是因为每台要 0.6~6 秒，两台串行就翻倍；不同显示器的 DDC 句柄互相独立，
    并行写是安全的（型号名读取早就这么干了）。
    allow_switch=False 关掉"切信号源"这最后一招（双线接法可避免切到空口）。
    expect_off=False 表示"屏幕可能本来就亮着"，此时先探测、亮着就跳过。
    返回 (成功序号, [(序号, 错误), ...])，错误文案带走到第几级。
    """
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    picked, bad = _select(targets, indices)
    # v1.9.6：只勾选部分显示器时，禁用系统级 SC_MONITORPOWER 重驱动
    # （广播会把没勾选的那台也黑一下）。全选时保持原行为，逐字节不变。
    allow_recycle = len(picked) == len(targets)
    results = {}

    def work(t):
        try:
            results[t["index"]] = _wake_one(t, allow_switch, expect_off, allow_recycle)
        except Exception as e:
            results[t["index"]] = (False, 0, str(e))

    threads = [threading.Thread(target=work, args=(t,)) for t in picked]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)

    ok, errs = [], list(bad)
    try:
        for t in picked:
            done, stage, why = results.get(t["index"], (False, 0, "超时未返回"))
            if done:
                ok.append(t["index"])
            else:
                errs.append((t["index"], "第 %d 级（%s）：%s"
                             % (stage, WAKE_STAGE_LABEL.get(stage, "-"), why)))
    finally:
        release_targets(targets)
    return ok, errs

# ---- 对外统一入口：按配置的熄灭方式分派 -----------------------------------
def _monitor_count():
    """当前显示器台数（只为报数，句柄立刻还掉）。"""
    targets = enumerate_targets()
    try:
        return len(targets)
    finally:
        release_targets(targets)

def _system_ok_targets(indices):
    """系统级动作没有"每台"的概念，用动作前选中的序号充数（空 = 全部）。"""
    return normalize_indices(indices) or list(range(_monitor_count()))

# ============================================================
# 快速点亮（v1.9.7）：0x60 信号源翻转，0 等待
# ------------------------------------------------------------
# 现场依据：两次独立实测（Legion R27qe Gen2 / DISPLAY2，16:57 与 17:00）
#   读 0x60 → 写 17 → 立即写回 15：动作 182~188ms，两次都把屏点亮，
#   读回 0x60 均回到原值 15。
# 为什么不用等：SetVCPFeature 返回只代表这次 DDC 写事务被面板 ACK，真正的点亮
# 来自「离开当前口→再回来」这一个边沿——DP 接收端必须重跑链路训练并重锁时序。
# 停在中途多久与重锁无关（v1.9.6 的 2 秒只是让第二次写不落在面板切换窗口里）。
#
# 边界：本路径只写 0x60；不写 0xD6 / 0x10，不发系统广播。
# 失败（读不到当前源 / 无可用中间源 / 写失败）一律回退 v1.9.6 的完整阶梯；
# 同一台 8 秒内第二次触发点亮时也升级为完整阶梯。
# ============================================================
FAST_FLIP_WINDOW_S = 8.0

_FAST_FLIP_AT = {}                 # 显示器标识 -> 上次触发的时间（monotonic 秒）

_FAST_FLIP_LOCK = threading.Lock()

_WAKE_INFO = []                    # 本次点亮的说明行（后台线程写，界面/CLI 读）

_WAKE_INFO_LOCK = threading.Lock()

def _wake_info(text):
    """追加一行本次点亮的说明（线程安全）。"""
    with _WAKE_INFO_LOCK:
        _WAKE_INFO.append(str(text))

def reset_wake_info():
    with _WAKE_INFO_LOCK:
        del _WAKE_INFO[:]

def take_wake_info():
    """取走并清空本次点亮的说明行；界面状态行 / CLI 输出用。"""
    with _WAKE_INFO_LOCK:
        out = list(_WAKE_INFO)
        del _WAKE_INFO[:]
    return out

def pick_flip_source(cur, declared):
    """纯函数：为 0x60 翻转挑一个**与当前值不同**的中间源。

    规则（按优先级）：
      1) 优先 17（HDMI-1，现场实测在这台 Legion 上有效）；
      2) 当前就是 17 时，从能力串声明的口里另选一个——优先 15（DP-1），
         其次按 15/16/17/18/19 的顺序；
      3) 能力串读不到时退回 MCCS 15~19 的通用候选（与 _switch_input_roundtrip 同口径）。
    没有可用候选（例如能力串只声明了当前这一个口）返回 None。
    ⚠️ 能力串里的数值是十六进制（见 _caps_inputs），这里接收的已是整数。
    """
    try:
        cur = int(cur)
    except Exception:
        return None
    cands = []
    for c in (declared or []):
        try:
            value = int(c)
        except Exception:
            continue
        if 15 <= value <= 19 and value not in cands:
            cands.append(value)
    if not cands:
        cands = [15, 16, 17, 18, 19]
    if 17 in cands and cur != 17:
        return 17
    for c in (15, 16, 17, 18, 19):
        if c in cands and c != cur:
            return c
    return None

def _fast_flip_recent(key, now=None):
    """这台屏在 8 秒内是否已经快速点亮过一次（只读，不改时间戳）。"""
    now = time.monotonic() if now is None else now
    with _FAST_FLIP_LOCK:
        last = _FAST_FLIP_AT.get(key)
        return last is not None and (now - last) <= FAST_FLIP_WINDOW_S

def _mark_fast_flip(key, now=None):
    """记下这台屏的快速点亮时刻（后续 8 秒内再点亮就升级完整阶梯）。"""
    now = time.monotonic() if now is None else now
    with _FAST_FLIP_LOCK:
        _FAST_FLIP_AT[key] = now

def _fast_flip_one(t):
    """对一台屏做 0x60 翻转，返回 (成功, 说明)。真实调用序列（无 sleep）：

      GetVCPFeatureAndVCPFeatureReply(h, 0x60)   读当前源
      SetVCPFeature(h, 0x60, 中间源)             切走
      SetVCPFeature(h, 0x60, 当前源)             立即切回

    ⚠️ 写本回失败时先立即补写一次（不等待），仍失败才判失败；
    并把「可能停在中间源」写进说明，避免界面报"已点亮"而屏其实不在原口。
    """
    t0 = time.perf_counter()
    h = t["handle"]
    got = _read_vcp(h, VCP_INPUT_SOURCE)
    if not got:
        return False, "读 0x60 失败"
    cur = int(got[0])
    # 只有当前源正好是 17 时才需要读能力串另选一个口；其余情况直接用 17，
    # 避免为了"快速"在缓存未命中时搭进去 3 秒的能力串实读。
    declared = []
    reason = ""
    if cur == 17:
        declared = _caps_inputs(_capabilities_cached(h, _display_key(t)))
    mid = pick_flip_source(cur, declared)
    if mid is None:
        return False, "0x60 无可用中间源（当前 %d）" % cur
    if cur == 17:
        reason = "（当前已是 17，按能力串声明改选 %d）" % mid
    if not _write_vcp(h, VCP_INPUT_SOURCE, mid):
        return False, "写 0x60=%d 失败（当前 %d）" % (mid, cur)
    if not _write_vcp(h, VCP_INPUT_SOURCE, cur):
        _write_vcp(h, VCP_INPUT_SOURCE, cur)          # 立即补写一次，不等待
        back = _read_vcp(h, VCP_INPUT_SOURCE)
        if not back or int(back[0]) != cur:
            return False, ("写回 0x60=%d 失败（现读 %s），屏可能停在中间源 %d"
                           % (cur, ("%d" % back[0]) if back else "—", mid))
    ms = (time.perf_counter() - t0) * 1000.0
    return True, "翻转 0x60 %d→%d→%d，耗时 %.1f ms%s" % (cur, mid, cur, ms, reason)

def screen_wake_flip(indices, allow_switch=True, expect_off=True):
    """快速点亮：逐台 0x60 翻转。与 screen_wake_many 同形状返回。

    全选/部分选择都只操作选中的屏，**绝不发系统广播**。
    同一台 8 秒内第二次触发、或翻转做不成时，该台回退到 _wake_one 完整阶梯。
    """
    targets = enumerate_targets()
    if not targets:
        raise RuntimeError(NO_MONITOR_MSG)
    picked, bad = _select(targets, indices)
    # 与 screen_wake_many 同一口径：只有全选时才允许升级路径里的系统级重驱动
    allow_recycle = len(picked) == len(targets)
    now = time.monotonic()
    results = {}

    def work(t):
        key = _display_key(t)
        try:
            if _fast_flip_recent(key, now):
                _mark_fast_flip(key, now)
                _wake_info("已升级为完整点亮（显示器 %d 在 8 秒内第二次点亮）" % t["index"])
                # 用户 8 秒内再点一次 = 第一次没亮：强制按"屏仍黑"走完整阶梯
                results[t["index"]] = _wake_one(t, allow_switch, True, allow_recycle)
                return
            if not expect_off:
                # 屏亮着、且没有近期的快速点亮记录：不翻转（避免可见黑屏），按旧行为
                results[t["index"]] = _wake_one(t, allow_switch, False, allow_recycle)
                return
            _mark_fast_flip(key, now)
            ok, note = _fast_flip_one(t)
            if ok:
                _wake_info("快速点亮：" + note)
                results[t["index"]] = (True, 1, note)
                return
            _wake_info("快速点亮不可用（%s），已回退完整阶梯" % note)
            results[t["index"]] = _wake_one(t, allow_switch, expect_off, allow_recycle)
        except Exception as e:
            results[t["index"]] = (False, 0, str(e))

    threads = [threading.Thread(target=work, args=(t,)) for t in picked]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)

    ok, errs = [], list(bad)
    try:
        for t in picked:
            done, stage, why = results.get(t["index"], (False, 0, "超时未返回"))
            if done:
                ok.append(t["index"])
            else:
                errs.append((t["index"], "第 %d 级（%s）：%s"
                             % (stage, WAKE_STAGE_LABEL.get(stage, "-"), why)))
    finally:
        release_targets(targets)
    return ok, errs
