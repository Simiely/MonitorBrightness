# 问题：深色主题下勾选框「看不出勾没勾」

**TL;DR**：`tk.Checkbutton` 的 `selectcolor` 会让**未勾选也有底色**；clam 主题的 ttk 勾选框
选中态图形不吃 `indicatorcolor`。正解是用 ttk 官方扩展 API —— `style.element_create` +
`style.layout` 换掉 `Checkbutton.indicator` 元素。

- **现象**：未勾选和已勾选的方块看着一样（都带色），用户直接反馈"都看不出来选中没有了"。
- **排查（都有依据，不靠猜）**：
  1. 查 **Tk 官方手册**（ttk_checkbutton man page）：`TCheckbutton` 可配置项只有
     `-background` / `-foreground` / `-indicatorbackground` / **`-indicatorcolor`** /
     `-indicatormargin` / `-indicatorrelief` / `-padding`。
     ⚠️ **没有 `-indicatorforeground`** —— 一开始用的这个名字是错的，等于没设。
  2. 读**本机 Tk 自带的主题源码** `tcl/tk8.6/ttk/clamTheme.tcl`（第 73–90 行）：
     clam 对勾选框只开放 `-indicatorbackground`（默认值还是 `#ffffff` 白底），
     选中态的图形是 C 层绘制，`-indicatorcolor` 不生效。
  3. **像素实测**：把一个 `text=""` 的勾选框按屏幕矩形取像素、转成字符图，
     选中态**没有任何白色像素** —— 证实"勾号换不了颜色"，深色底下就是看不清。
  4. 换 `alt` 主题能画出正常对勾，但它的指示器底色不跟深色配置。
- **解决**：走 ttk 官方扩展机制（`ttk::style` 的 `element_create` / `layout`）：
  用两张**纯代码生成**的 `PhotoImage`（未勾=深色空框；勾上=强调色底+白勾）注册成
  `Dark.tickbox`，再把 `Checkbutton.indicator` 从 layout 里替换掉。
  这样**仍然是真正的 `ttk.Checkbutton`**，变量绑定、键盘空格、禁用态行为全部保留，
  也不需要外部图片资源（守约定：单文件交付）。
- **踩到的坑**：`style.layout` 里给 `Checkbutton.label` 传 `padding` 会报
  `TclError: Invalid -children value`；间距要用文档里的 `-indicatormargin`（左 上 右 下）。
- **预防**：新增控件视觉前，先在**放大截图/像素图**上确认状态可辨，别只看缩略图。
