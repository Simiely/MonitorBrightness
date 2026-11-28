# -*- coding: utf-8 -*-
"""布局 / 拓扑控制器（R4-4，从 monitor_brightness.py 逐字搬出）。"""
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

from core.config import _atomic_write_text, save_config
from core.logging import dlog
from core.paths import CONFIG_DIR
from domain.decisions import (
    ccd_friendly_name, is_vdd_index, plan_model_updates, restore_selection,
)
from hw.ccd import (
    _ccd_api, _ccd_apply, _ccd_make_snapshot, _ccd_query_raw, _ccd_wait_state,
    ccd_probe_topology, screen_verdict,
)
from hw.ccd_structs import (
    _CcdModeInfo, _CcdPathInfo, _CCD_APPLY_FLAGS, _SDC_ALLOW_CHANGES,
    _SDC_APPLY, _SDC_SAVE_TO_DATABASE,
)
from hw.ddc import _display_key, _dxva2, enumerate_targets, monitor_model, release_targets
from hw.geometry import _ccd_layout_by_anchor
from hw.topology import topology_off
from schedule_report import _as_ok_errs
from service import (
    resolve_monitors, selection_anchors, topology_enable_anchor,
)
from ui.sheets import HeirSheet
from app.common import selection_payload

# ---- 默认双屏布局快照（v2.01.12） ----------------------------------------
# 「恢复双屏」按钮要回到的是"用户记住的那一版默认布局"。整份 CCD 字节写回
# 是唯一同时做到「接回物理屏 + 让 VDD 退场 + 摆回原位」的办法（普通 topology_on
# 在 VDD 占着 (0,0) 时会把物理屏挤到桌面最右，参见 17:40 的 diag.log）。
# 快照跟 monitor_positions 一起记：**只在用户主动点「记住布局」时写**，
# 不自动覆盖（交接主屏那一刻的布局不是用户摆的，见 v2.01.11）。
DEFAULT_LAYOUT_FILE = os.path.join(CONFIG_DIR, "default_layout.json")
_SDC_VALIDATE = 0x00000040
# 必须与 _CCD_APPLY_FLAGS 完全同一套旗标，只把 APPLY 换成 VALIDATE，
# 否则会 rc=87 假失败（真机 18:5x 实测：漏掉 SAVE_TO_DATABASE 就拒）。
# 口径与 scripts/topology_handover_probe.py / topology_repro87.py 一致。
_VALIDATE_FLAGS = ((_CCD_APPLY_FLAGS | _SDC_VALIDATE)
                   & ~(_SDC_APPLY | _SDC_SAVE_TO_DATABASE))


def topology_heir_candidates(off_anchors=(), rows=None):
    """能接班当主屏的屏：连着、且不在这次要断开的名单里。

    物理屏排前面（用户看得见），虚拟屏排后面 —— 只有"全部物理屏都要断"
    的时候才会轮到它，那正是串流场景。
    """
    rows = rows if rows is not None else ccd_probe_topology()
    skip = set(off_anchors or ())
    out, seen = [], set()
    for r in rows:
        anchor = r.get("anchor")
        if not r.get("connected") or not anchor or anchor in skip or anchor in seen:
            continue
        seen.add(anchor)
        out.append({"anchor": anchor,
                    "friendly": r.get("friendly") or anchor,
                    "vdd": bool(r.get("vdd")),
                    "active": bool(r.get("active")),
                    "gdi": r.get("gdi") or ""})
    out.sort(key=lambda c: (c["vdd"], not c["active"]))
    return out

def remember_layout_snapshot(cfg):
    """把"当前拓扑"记成用户的显示器布局（v2.01.11）。

    位置记录**不会自动覆盖**：交接主屏会把接班屏挪到桌面原点，那一刻的
    "真实位置"不是用户摆的。这条是唯一的主动更新入口，放主线程同步做
    （一次 CCD 查询 ~50ms，不值得异步）。
    返回 (布局, None) 或 (None, 错误文案)。
    """
    u32 = _ccd_api()
    raw = _ccd_query_raw(u32) if u32 is not None else None
    if raw is None:
        return None, "读不到当前显示器拓扑"
    paths, modes, pc, mc, rows = raw
    layout = _ccd_layout_by_anchor(rows, paths, modes)
    if not layout:
        return None, "当前没有活动的显示器"
    cfg["monitor_positions"] = {a: list(r) for a, r in layout.items()}
    ok, err = save_config(cfg)
    if not ok:
        return None, err or "配置保存失败"
    dlog("app.remember_layout", layout=cfg["monitor_positions"])
    return cfg["monitor_positions"], None

def save_default_layout():
    """把当前完整 CCD 布局存成"默认双屏"快照。

    返回 (简述, 错误文案)。简述里带屏数与友好名，给状态卡显示。
    快照里的 bytes 已按 count 截断（_ccd_slice_bytes），不会越界。
    """
    u32 = _ccd_api()
    raw = _ccd_query_raw(u32) if u32 is not None else None
    if raw is None:
        return None, "读不到当前显示器拓扑"
    paths, modes, pc, mc, rows = raw
    visible = [r for r in rows
               if r.get("active") and r.get("connected") and r.get("anchor")]
    # 同一块屏在 QDC_ALL_PATHS 下有多条 path，按锚点去重
    anchors = list(dict.fromkeys(r["anchor"] for r in visible))
    if not anchors:
        return None, "当前没有活动的显示器"
    snap = _ccd_make_snapshot(paths, modes, pc, mc, anchors)
    snap["note"] = "用户记住的默认双屏布局；「恢复双屏」按这份整份写回"
    try:
        _atomic_write_text(DEFAULT_LAYOUT_FILE,
                           json.dumps(snap, ensure_ascii=False, indent=2))
    except Exception as e:
        return None, str(e)
    dlog("app.save_default_layout", anchors=anchors, pc=pc, mc=mc)
    return "%d 块屏（%s）" % (len(anchors), ", ".join(
        r["friendly"] or "显示器" for r in
        {a: next(x for x in visible if x["anchor"] == a) for a in anchors}.values())), None

def _layout_snapshot_error(snap):
    """校验快照形状；返回 "" 表示可用。

    P0-3：`pathCount/modeCount` 来自本地 JSON，不能直接拿去分配 ctypes 数组。
    先卡上限（32 paths / 256 modes）和 hex 字节数，畸形文件只报错、不分配。
    """
    if not isinstance(snap, dict):
        return "默认布局快照格式不对"
    try:
        pc = int(snap.get("pathCount"))
        mc = int(snap.get("modeCount"))
    except (TypeError, ValueError):
        return "默认布局快照格式不对"
    if not (1 <= pc <= 32 and 1 <= mc <= 256):
        return "默认布局快照与当前显示器结构不符"
    pb = snap.get("pathsHex")
    mb_ = snap.get("modesHex")
    if not isinstance(pb, str) or not isinstance(mb_, str):
        return "默认布局快照格式不对"
    if len(pb) != pc * ctypes.sizeof(_CcdPathInfo) * 2:
        return "默认布局快照与当前显示器结构不符"
    if len(mb_) != mc * ctypes.sizeof(_CcdModeInfo) * 2:
        return "默认布局快照与当前显示器结构不符"
    return ""


def load_default_layout():
    """读回默认布局快照；文件不存在/坏掉返回 (None, 原因)。"""
    try:
        with open(DEFAULT_LAYOUT_FILE, "r", encoding="utf-8") as f:
            snap = json.load(f)
        err = _layout_snapshot_error(snap)
        if err:
            return None, err
        return snap, None
    except FileNotFoundError:
        return None, "还没记住默认布局"
    except Exception as e:
        return None, "读默认布局失败：%s" % e

def restore_default_layout(validate_only=False, snap=None):
    """把默认双屏布局整份写回。

    先 SDC_VALIDATE 校验提交形态（只读、不碰桌面），rc=0 才真正 APPLY。
    长度对不上/校验失败一律不写。返回 (错误文案 或 None)。

    `validate_only=True` 只做校验，给按钮预检用；`snap` 可注入，便于单测。
    """
    if snap is None:
        snap, err = load_default_layout()
        if err:
            return err
    err = _layout_snapshot_error(snap)
    if err:
        dlog("app.restore_default.fail", stage="shape", err=err)
        return err
    u32 = _ccd_api()
    if u32 is None:
        return "恢复双屏仅支持 Windows"
    try:
        u32.SetDisplayConfig.argtypes = [
            wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        u32.SetDisplayConfig.restype = wintypes.LONG
    except (AttributeError, TypeError):
        pass                      # 测试假对象没有 ctypes 的 argtypes

    def build():
        paths = (_CcdPathInfo * int(snap["pathCount"]))()
        modes = (_CcdModeInfo * int(snap["modeCount"]))()
        pb = bytes.fromhex(snap["pathsHex"])
        mb_ = bytes.fromhex(snap["modesHex"])
        if len(pb) != ctypes.sizeof(paths) or len(mb_) != ctypes.sizeof(modes):
            return None, None, None, "快照与当前显示器结构不符，拒绝写回"
        if len(pb):
            ctypes.memmove(ctypes.byref(paths), pb, len(pb))
        if len(mb_):
            ctypes.memmove(ctypes.byref(modes), mb_, len(mb_))
        return paths, modes, (snap["pathCount"], snap["modeCount"]), None

    paths, modes, counts, err = build()
    if err:
        dlog("app.restore_default.fail", stage="shape", err=err)
        return err
    pc, mc = counts
    rc = u32.SetDisplayConfig(pc, ctypes.byref(paths), mc, ctypes.byref(modes),
                              _VALIDATE_FLAGS)
    dlog("app.restore_default.validate", pc=pc, mc=mc,
         flags="0x%08X" % _VALIDATE_FLAGS, rc=rc)
    if rc != 0:
        return "默认布局已和当前硬件不匹配（校验 rc=%d），请重新「记住布局」" % rc
    if validate_only:
        return None
    # 校验通过 → 用带 SAVE_TO_DATABASE 的正式 flags 提交
    rc = _ccd_apply(u32, paths, modes, pc, mc)
    if rc != 0:
        dlog("app.restore_default.fail", stage="apply", rc=rc)
        return "恢复失败：SetDisplayConfig rc=%d（拓扑未改动）" % rc
    anchors = list(snap.get("targetAnchors") or [])
    # v2.01.19：快照可能是在串流形态（VDD 当主屏）下记的，此时 targetAnchors
    # 含 VDD。_ccd_wait_state 的 allow_vdd=False 会把 VDD 过滤掉，导致
    # wanted 里的 VDD 永远等不到 → 误报"2 秒内没读到确认"（实际已写回）。
    # 所以只拿物理屏锚点做回读验证。
    try:
        vdd_anchors = {r["anchor"] for r in ccd_probe_topology()
                       if r.get("vdd") and r.get("anchor")}
    except Exception:
        vdd_anchors = set()
    phys = [a for a in anchors if a not in vdd_anchors]
    ok = _ccd_wait_state(phys, True) if phys else True
    dlog("app.restore_default.done", anchors=anchors, verified=ok,
         vdd_skipped=sorted(vdd_anchors & set(anchors)))
    return None if ok else "布局已写回，但 2 秒内没读到确认，请「检测」看结果"


class _LayoutActions:
    """布局：拓扑断开/接回、主屏交接、位置记忆、显示器数据。

    v2.03.0 U5-2：从 App 里按职责切出。方法体一字未改（`self.xxx`
    仍指向最终的 App 实例），因此零行为变化。将来的物理分离只需
    把这些类搬到独立文件再 import —— 那时才算真正的模块化。
    """

    def remember_layout(self):
        """把现在的显示器摆法记成"用户的布局"，断开/接回都按这份来。"""
        layout, err = remember_layout_snapshot(self.cfg)
        if err:
            self.status_var.set("记不住布局：%s" % err)
            self.msg.showerror("记住布局失败", err)
            return
        dlog("app.action", what="remember_layout", count=len(layout))
        # v2.01.12：位置与完整 CCD 快照一起记，供「恢复双屏」整份写回
        desc, snap_err = save_default_layout()
        if snap_err:
            self.status_var.set("位置已记住，但默认布局快照没存上：%s" % snap_err)
            self._flash_remember_btn("快照失败", err=True)
            return
        self.status_var.set("已记住 %d 块屏的位置和布局（%s）" % (len(layout), desc))
        self._flash_remember_btn("已记住 ✓")

    def _flash_remember_btn(self, text, err=False):
        """在「记住」按钮上短暂显示结果（v2.01.25）。

        设置弹层是模态的，会挡住主窗口状态卡；不给就地回执，用户就以为没反应。
        2 秒后复原；弹层先被关掉时 widget 已销毁，静默跳过。
        """
        btn = getattr(self, "_remember_btn", None)
        if btn is None:
            return
        try:
            btn.set_text(text)
            if err:
                btn.after(2600, lambda: self._restore_remember_btn(btn))
            else:
                btn.after(1800, lambda: self._restore_remember_btn(btn))
        except Exception:
            pass

    def _restore_remember_btn(self, btn):
        try:
            if btn.winfo_exists():
                btn.set_text("记住")
        except Exception:
            pass

    # ---- 恢复双屏（倒计时） ------------------------------------------------
    def ask_restore_default(self):
        """点「恢复双屏」：先校验默认布局还在且与硬件匹配，再启动倒计时。"""
        if getattr(self, "_restore_after", None) is not None:
            return
        err = restore_default_layout(validate_only=True)
        if err:
            dlog("app.action", what="restore_default", stage="precheck", err=err)
            self.status_var.set(err)
            self.msg.showwarning("恢复双屏不可用", err)
            return
        secs = max(0, min(120, int(self.cfg.get("restore_delay", 10))))
        dlog("app.action", what="restore_default", stage="countdown", seconds=secs)
        self._restore_deadline = time.monotonic() + secs
        self._tick_restore()

    def _tick_restore(self):
        """每秒刷一次剩余秒数；到点执行恢复。"""
        left = int(self._restore_deadline - time.monotonic() + 0.999)
        if left > 0:
            self.status_card.set_restore_countdown(left)
            self._restore_after = self.root.after(200, self._tick_restore)
        else:
            self._restore_after = None
            self.status_card.set_restore_countdown(None)
            self._do_restore_default()

    def cancel_restore_default(self):
        if getattr(self, "_restore_after", None) is not None:
            self.root.after_cancel(self._restore_after)
            self._restore_after = None
        self.status_card.set_restore_countdown(None)
        dlog("app.action", what="restore_default", stage="cancel")
        self.status_var.set("已取消恢复双屏")

    def _do_restore_default(self):
        """倒计时到点：整份写回默认双屏布局（后台线程，不卡 UI）。"""
        # v2.01.20（走查 P-22）：到点时如果正有屏幕动作在跑（用户刚点了
        # 熄灭/点亮），两边都改拓扑会互相覆盖，_busy 的归属也会错乱
        # （先完成的那个把按钮启用了，另一个还在跑）。直接取消并说明。
        if self._busy:
            self.status_var.set("有屏幕操作正在进行，恢复双屏已取消，请稍后重试")
            dlog("app.action", what="restore_default", stage="cancel_busy",
                 busy_action=self._busy_action)
            return

        def job():
            return restore_default_layout()

        def done(err):
            if err:
                self.status_var.set(err)
                self.msg.showerror("恢复双屏失败", err)
            else:
                dlog("app.action", what="restore_default", stage="done")
                self.status_var.set("已恢复默认双屏布局")
                self.refresh_monitors(silent=True)
        self.run_bg(job, done, busy=True)

    def _restore_selection(self, prev):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return restore_selection(prev, len(self.monitors))

    def _first_run_detect(self):
        """首次启动补一次全量检测，并补写显示器锚点（v2.01.17，v2.01.19 修）。

        v2.01.19 修：原实现无条件 `list(range(len(self.monitors)))`，把老配置
        里"用户只勾了一块屏"的选择**静默改成全选**。现在按配置解析：
        `resolve_monitors()` 返回 [] 才表示"配置里确实没有选择信息"= 全选；
        有选择就按原选择补锚点，不动物理勾选语义。
        """
        if not self.monitors:
            return
        # 全量检测一次（启动链路里已有一次 probe_screens，这里再补一次是
        # 为了 after_idle 时窗口已可见、显示器句柄都稳定了，读数更可靠）
        self.probe_screens()
        idx = resolve_monitors(self.cfg) or list(range(len(self.monitors)))
        anchors = selection_anchors(idx)
        if not anchors:
            dlog("app.first_run_detect", err="拿不到锚点", idx=idx)
            return
        # v2.01.24（走查 R-02 转正）：只写 anchors，**不动 monitors/monitor_ids**。
        # 用户配的 `monitors: []` 是"全部"语义；写成 [0,1] 会把"全部"固化成
        # "这两块"，以后插新屏不会自动纳入。anchors 只用来让定时任务认得住屏，
        # 不承担"范围"语义。
        self.cfg["monitor_anchors"] = anchors
        ok, err = save_config(self.cfg)
        dlog("app.first_run_detect", idx=idx, anchors=anchors, saved=ok, err=err)
        if ok:
            self.status_var.set("已自动识别 %d 块显示器" % len(idx))

    def is_vdd(self, index):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return is_vdd_index(index, self.monitors,
                            getattr(self, "_vdd_devs", ()))

    def ccd_friendly(self, index):
        """转发到模块级纯函数（v2.03.0 P2）。"""
        return ccd_friendly_name(index, self.monitors,
                                 getattr(self, "_ccd_names", {}))

    def _apply_models(self, models):
        """把读到的型号刷到界面上（判断由 plan_model_updates 负责）。"""
        changed = False
        for i, name in plan_model_updates(models, self.mon_models,
                                          len(self.monitors)):
            self.mon_models[i] = name
            self.mon_panel.set_row_text(i, name)
            changed = True
        if changed:
            self.cfg["models"] = {str(k): v for k, v in self.mon_models.items() if v}
            save_config(self.cfg)

    def _fetch_models(self):
        """显示器型号缓存没有的补一次（写入行标题与配置）。"""
        targets = self.selected_indices() or list(range(len(self.monitors)))
        # VDD 不读型号：它没有真实 EDID，按 index 缓存下来的名字会串到
        # 物理屏行（或反过来，VDD 当主屏时套用物理屏名）—— v2.01.12 修
        missing = [i for i in targets
                   if i not in self.mon_models and 0 <= i < len(self.monitors)
                   and not self.is_vdd(i)]
        if not missing:
            return

        def job_models():
            out = {}
            targets_now = enumerate_targets()
            lib = _dxva2()
            used = [t for t in targets_now if t["index"] in missing]

            def one(t):
                try:
                    out[t["index"]] = monitor_model(t["handle"], _display_key(t))
                finally:
                    # 同 screen_wake_many：句柄由线程自己销毁，避免 join 超时后 UAF
                    try:
                        lib.DestroyPhysicalMonitor(t["handle"])
                    except Exception:
                        pass

            threads = [threading.Thread(target=one, args=(t,), daemon=True) for t in used]
            for th in threads:
                th.start()
            for th in threads:
                th.join(20)
            release_targets([t for t in targets_now if all(t is not u for u in used)])
            return {i: v for i, v in out.items() if v}

        self.run_bg(job_models, self._apply_models)

    def _render_states(self, result):
        self._probing = False
        self._brighting = False
        if isinstance(result, Exception):
            self.status_var.set("检测失败：%s" % result)
            return
        states, bright = result
        self._screen_states = {s["index"]: s["state"] for s in states
                               if isinstance(s, dict) and "index" in s and "state" in s}
        # 启动早期可能还没建面板；建了才让它按最新亮灭重画行内按钮
        panel = getattr(self, "mon_panel", None)
        if panel is not None:
            panel.refresh_buttons()
        if not states:
            # 轻量刷新（只读亮度）：不动亮灭结论，只更新提示行
            if bright and self._last_verdict:
                self._update_hint_brightness(bright)
            return
        v = screen_verdict(self.cfg, states)
        # v2.01.0：这里以前会在"被键鼠唤醒"时把状态回写落盘。程序不再记账，
        # 结论就是当次探测的结果，不需要也不应该往配置里写运行态。
        if v["state"] != "off":
            vals = [bright[i] for i in sorted(bright) if bright.get(i) is not None]
            if vals and len(set(vals)) == 1:
                hint = "当前 %d%% · %d 台显示器" % (vals[0], len(states))
            elif vals:
                hint = "当前 %s%% · %d 台显示器" % (
                    "/".join(str(x) for x in vals), len(states))
            else:
                hint = "%d 台显示器 · 亮度可调" % len(states)
        else:
            hint = "键鼠输入不会点亮它；点「点亮屏幕」恢复"
        # 状态未知时，把"为什么不知道"直接摆在提示行——否则用户点一次「检测」
        # 只看到一片空白，不知道程序到底读了什么。
        if v["state"] == "unknown" and v.get("evidence"):
            hint = hint + " · " + v["evidence"]
        self.status_card.render(self._status_payload(v["state"], hint))
        self._last_verdict = v
        self._fit_window()

    def _remember_selection(self):
        idx, ids = selection_payload(self.selected_indices())
        self.cfg["monitors"] = idx
        self.cfg["monitor_ids"] = ids
        self.cfg["monitor_anchors"] = selection_anchors(idx)

    def _ask_heir(self, index, off_anchors):
        """弹窗让用户挑一块屏接任主显示器。"""
        cands = topology_heir_candidates(off_anchors)
        # 诊断（v2.01.13）：候选数用户报过"该有 2 个却只显示 1 个"。
        # 这里把候选来源与每块屏的状态落日志，下次出现时一眼能看出
        # 是 targetAvailable=0 还是查询失败——只记录，不改任何行为。
        dlog("app.heir_prompt", off=list(off_anchors), count=len(cands),
             candidates=[{"anchor": c["anchor"], "vdd": c["vdd"],
                          "active": c["active"]} for c in cands])
        if not cands:
            self.msg.showwarning(
                "没有屏可以接任主显示器",
                "断开 %d 之后桌面上一块屏都不剩，Windows 不允许这样做（会黑屏）。\n\n"
                "先把另一块显示器的线接上，或让虚拟显示器（串流）先用起来，再试。"
                % index)
            return
        name = "显示器 %d" % index
        if index < len(self.mon_models) and self.mon_models.get(index):
            name = self.mon_models[index]

        def picked(anchor):
            self._busy_action = "off"
            self._row_action = True
            self.status_var.set("正在把主显示器交给另一块，然后断开 %s…" % name)
            self.run_bg(lambda: topology_off([index], self.cfg, heir=anchor),
                        self._finish_screen_off, busy=True)

        try:
            self._sheet = HeirSheet(self, cands, name, picked)
        except Exception as e:
            self.msg.showerror("打开选择框失败", "%s: %s" % (type(e).__name__, e))

    def topology_enable(self, anchor):
        """GUI「显示」按钮（断开的屏那一行）：后台接回指定锚点的屏（不碰 Tk）。"""
        dlog("app.action", what="topology_enable", anchor=anchor)
        if self._busy:
            self._queue_screen_action("on")
            return
        self._busy_action = "on"
        self.status_var.set("正在启用显示器…")

        def job():
            return topology_enable_anchor(self.cfg, anchor)

        self.run_bg(job, self._finish_topology_enable, busy=True)

    def _finish_topology_enable(self, result):
        ok, errs = _as_ok_errs(result)
        if ok:
            save_config(self.cfg)
            self.status_var.set("显示器已启用")
            self.status_card.render(self._status_payload("on", "亮度与定时均正常"))
            self.refresh_monitors(silent=True)
        for i, e in errs:
            self.status_var.set("启用失败：" + (e if i < 0 else "显示器 %d: %s" % (i, e)))
        if errs:
            self.msg.showwarning("启用失败", "\n".join(
                (e if i < 0 else "显示器 %d: %s" % (i, e)) for i, e in errs))
        self.probe_screens()
