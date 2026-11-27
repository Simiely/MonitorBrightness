# 问题：GUI 卡在「当前亮度: 读取中…」

**TL;DR**：两个原因叠加 —— 跨线程碰 Tk 控件，以及把 0.1 秒的亮度读取和 3 秒的型号读取
串在一条线程里。

- **根因**：
  1. 后台线程里调 `self.root.after(0, ...)` 抛 `RuntimeError: main thread is not in main loop`
     （Tk 非线程安全）。
  2. 实测：`get_brightness_many` 只要 **0.13s**，而 `monitor_model`（MCCS 能力串）要 **3.1s/台**。
     两者串行时，亮度结果被型号名拖住。
- **解决**：改为「后台线程 → `queue.Queue` → 主线程 `after` 轮询」，
  界面只由主线程更新；并把**亮度**与**型号名**拆成两条独立后台任务，
  亮度先出、型号名稍后补（补到后只刷新列表标签）。
  现在统一收敛成 `run_bg(job, on_done, busy)` 一个入口。
- **预防**：后台任务计数归零才停止轮询，避免空转。
