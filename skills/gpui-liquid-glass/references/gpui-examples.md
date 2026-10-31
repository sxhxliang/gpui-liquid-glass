# GPUI 接入示例

本页是 [implementation.md](implementation.md) 中玻璃模块的实际接法，来自一个 GPUI Kit 0.8.1 应用的主窗口、设置窗口和图片预览窗口。尺寸是该应用的取值，统一经过宿主的 `z()` 缩放；按产品的设计规则调整。

页内的代码块按顺序拼在一起就是一个可编译的模块，共用第一段的导入，并调用应用自己的主题函数 `apply_theme`。这些代码块已与参考实现一起通过 `cargo check` 和 `cargo clippy`。

```rust
use gpui_kit::component::resizable::{h_resizable, resizable_panel};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::*;

use crate::glass::{self, Backdrop, Pane};
use crate::host::{Palette, is_dark, z};
```

## 开关与窗口

玻璃状态只在一个地方决定：设置开启并且平台支持。渲染代码只读 `glass::active(cx)`；非 macOS 平台和测试构建中它恒为 false，原有界面不变。

```rust
/// Options for a window that may open on glass. The background starts
/// transparent, so the first frame is not an opaque flash, and the traffic
/// lights start where the glass layout wants them.
fn glass_window_options(setting_on: bool) -> WindowOptions {
    WindowOptions {
        titlebar: Some(TitlebarOptions {
            title: Some("App".into()),
            appears_transparent: true,
            traffic_light_position: Some(glass::traffic_light_position(setting_on)),
        }),
        window_background: if setting_on && glass::supported() {
            WindowBackgroundAppearance::Transparent
        } else {
            WindowBackgroundAppearance::Opaque
        },
        ..Default::default()
    }
}

/// Turn glass on or off while windows are open.
fn set_liquid_glass(on: bool, cx: &mut App) {
    glass::apply(on, cx);
    // The app's full theme step: it rebuilds the GPUI Kit tokens from the
    // palette and ends with `glass::adapt_theme`. Calling `adapt_theme`
    // alone cannot take the glass colour tokens back off.
    apply_theme(cx);
    // Every window switches layout and material on its next render.
    cx.refresh_windows();
}
```

在 `open_window` 的构建闭包里，先调用 `glass::apply(setting_on, cx)`，再执行主题，最后构建根视图。

**红绿灯。** 参考应用只在创建窗口时设置红绿灯位置，切换玻璃后要重开窗口才会移动。GPUI 从 gpui-pre 0.3.9 起在 macOS 上提供 `Window::set_traffic_light_position`：它保存新位置，之后每次布局都会重新应用。在窗口的玻璃状态变化时调用下面的函数，红绿灯就能立即移动。这一点来自源码核对，尚未在真实窗口中验证。

```rust
/// Move the traffic lights after this window's glass state changed. GPUI
/// keeps the position and re-applies it on every layout.
#[cfg(target_os = "macos")]
fn place_traffic_lights(window: &Window, cx: &App) {
    window.set_traffic_light_position(glass::traffic_light_position(glass::active(cx)));
}
```

参考位置：悬浮侧栏内为 `(INSET + 12, INSET + 13)`；顶部是整条玻璃工具栏的窗口，红绿灯垂直居中于工具栏；内容从 `TRAFFIC_END`（红绿灯右侧）开始。

## 背景链

玻璃区域上方只要有一层画了底色，材质就看不见。实际排查到的有这些层：

1. **GPUI Kit `Root`。** 它的插件给根表面刷主题背景。`Root` 先应用插件样式，再应用自身的 `style`，所以 `sync_window` 覆盖 `root.style().background` 就够了。
2. **应用外层包装。** 例如授权页这类包住主视图的根，原本会整窗铺底色；改为只在玻璃未生效时铺。
3. **各窗口的根和外框行。** 应用根、工具栏行、标签行，以及设置窗口和预览窗口的根，都改成 `.when(!glass, |el| el.bg(rgb(pal.bg)))`。
4. **自带底色的组件。** GPUI Kit `TitleBar` 等组件在玻璃下设为 `transparent_black()`，并去掉底部分隔线。
5. **面板自身。** 侧栏、胶囊等面板的父容器只设圆角，不设背景。

玻璃开启后，整窗底色只由原生底色层提供：主题色，在桌面背景的面板处镂空。用红色底色层定位遮挡层的方法见 [macos-bridge.md](macos-bridge.md#排查)。

## 窗口根

```rust
/// A window root on glass: sync first, panes anywhere inside, commit last.
/// Windows opened without the palette (tests) skip the commit.
fn glass_root(window: &mut Window, cx: &mut App, children: Vec<AnyElement>) -> Div {
    glass::sync_window(window, cx);
    let glass = glass::active(cx);
    let pal = cx.try_global::<Palette>().copied();
    div()
        .relative()
        .size_full()
        .when_some(pal.filter(|_| !glass), |root, pal| root.bg(rgb(pal.bg)))
        .children(children)
        .children(pal.map(|pal| glass::commit(pal.bg, is_dark(&pal))))
}
```

浮层、弹窗等需要盖在一切之上的子元素仍然排在 `commit` 之前；`commit` 是零尺寸元素，不影响它们的层级。

## 主窗口

```text
窗口（透明）
├─ 悬浮侧栏列（宽度动画）  pane("sidebar", Desktop)；头部容纳红绿灯与设置按钮
└─ 内容列
   ├─ 工具栏行（无底色）    capsule("toolbar-lead") … 拖动区 … capsule("toolbar-trail")
   ├─ 标签行（无底色）      轨道 pane("project-tab-bar", Window) + capsule("project-tab-new")
   ├─ 编辑器
   └─ 停靠格式栏            pane("format-toolbar", Window)，alpha 跟随出现动画
根元素最后：commit(pal.bg, is_dark(&pal))
```

### 悬浮侧栏

```rust
/// The floating sidebar: a desktop-backed pane `INSET` from the window
/// edges, its header leaving room for the traffic lights. `column_width`
/// animates between 0 and `width + INSET`: the pane keeps its width and
/// slides out past the window's left edge instead of being clipped, so the
/// native glass, which ignores GPUI clipping, follows it exactly.
fn glass_sidebar(
    width: f32,
    column_width: f32,
    header: impl IntoElement,
    body: impl IntoElement,
) -> Div {
    let radius = px(glass::SIDEBAR_RADIUS);
    let pane = div()
        .absolute()
        .top(px(glass::INSET))
        .bottom(px(glass::INSET))
        .right_0()
        .w(z(width))
        .rounded(radius)
        .flex()
        .flex_col()
        .child(glass::pane("sidebar", Pane::new(radius, Backdrop::Desktop)))
        .child(
            div()
                .h(z(40.))
                .flex_none()
                .flex()
                .items_center()
                .justify_end()
                .pr(z(6.))
                .child(header),
        )
        .child(div().flex_1().min_h_0().child(body));
    div()
        .relative()
        .flex_none()
        .h_full()
        .w(z(column_width))
        .child(pane)
}
```

- 显隐动画放在外层列上，例如用 `with_animation` 在 `0` 和 `width + INSET` 之间插值，展开约 280 ms、收起约 200 ms。
- 拖动调整宽度的把手只放在圆角之间的直线段，即 `top(radius).bottom(radius)`。
- 侧栏内各行的选中、悬停和分隔线改用 `glass::fills(pal, true)`；实色方块会盖住材质。

### 工具栏胶囊

```rust
/// The toolbar on glass: no fill of its own, buttons grouped on capsules,
/// and room for the traffic lights while the sidebar is hidden.
fn glass_toolbar(lead: Vec<AnyElement>, trail: Vec<AnyElement>, sidebar_visible: bool) -> Div {
    div()
        .h(z(48.))
        .flex_none()
        .flex()
        .items_center()
        .gap(z(8.))
        .pl(if sidebar_visible {
            z(4.)
        } else {
            px(glass::TRAFFIC_END)
        })
        .pr(z(9.))
        .child(glass::capsule("toolbar-lead", z(34.)).children(lead))
        // The window drag region.
        .child(div().flex_1().h_full())
        .child(glass::capsule("toolbar-trail", z(34.)).children(trail))
}
```

胶囊 34 高，里面是 30 的圆形按钮，由 `glass::icon_btn` 构造。玻璃与非玻璃两种按钮共用一个构造函数，hover 和按下的填充作为参数传入。不要先构造普通按钮再补一次 `.hover()`：GPUI 只允许设置一次，第二次会 panic。

### 标签轨道

```rust
/// Tabs on a glass track. The track's padding and radius make it
/// concentric with the tab pills inside: outer radius = inner + padding.
fn tab_track(tab_height: Pixels, tabs: impl IntoElement) -> Div {
    let radius = tab_height / 2. + z(2.);
    div()
        .relative()
        .flex_1()
        .min_w_0()
        .flex()
        .p(z(2.))
        .rounded(radius)
        .child(glass::pane(
            "project-tab-bar",
            Pane::new(radius, Backdrop::Window),
        ))
        .child(tabs)
}
```

选中的标签是半透明药丸（`fills.active`），悬停用 `fills.hover`。原来的实色标签栏背景，以及为侧栏下方补色的色块，在玻璃下都不再渲染。“新建”按钮单独放在一个小胶囊里。

### 停靠的格式栏

原本浮在正文上方的底部格式栏不能用真玻璃：GPUI 会把正文画在玻璃上面。玻璃下把它改为编辑器列里的普通流式元素，正文不再从它下方经过。

```rust
/// A bar docked below the editor rather than floating over its text, on a
/// glass capsule that fades with the bar; `phase` runs from 0 to 1.
fn docked_bar(phase: f32, content: AnyElement) -> Div {
    let phase = phase.clamp(0., 1.);
    div()
        .w_full()
        .flex()
        .justify_center()
        .flex_shrink_0()
        .opacity(phase)
        // Stop registering once invisible: the next commit removes the view.
        .when(phase > 0.001, |bar| {
            bar.px(z(12.)).pt(z(6.)).pb(z(10.)).child(
                div()
                    .relative()
                    .rounded(z(20.))
                    .child(glass::pane(
                        "format-toolbar",
                        Pane {
                            alpha: phase,
                            ..Pane::new(z(20.), Backdrop::Window)
                        },
                    ))
                    .child(content),
            )
        })
}
```

`.opacity(phase)` 只影响 GPUI 绘制的内容，原生玻璃要靠 `Pane::alpha` 同步淡出。

## 独立窗口

每个独立窗口都按“窗口根”一节接入：render 开头 `sync_window`，根元素最后 `commit`。只在主窗口提交，其他窗口不会出现玻璃，还可能串到主窗口。

### 设置窗口：Tahoe 系统设置布局

```rust
/// Settings on glass, after Tahoe's System Settings: a floating
/// desktop-backed navigation pane that carries the traffic lights, with
/// the page beside it under a slim drag strip.
fn settings_layout(nav: impl IntoElement, page: impl IntoElement) -> Div {
    let radius = px(glass::SIDEBAR_RADIUS);
    let rail = div()
        .flex_none()
        .h_full()
        .w(z(204.) + px(glass::INSET))
        .pl(px(glass::INSET))
        .py(px(glass::INSET))
        .child(
            div()
                .relative()
                .size_full()
                .rounded(radius)
                .flex()
                .flex_col()
                .child(glass::pane(
                    "settings-navigation",
                    Pane::new(radius, Backdrop::Desktop),
                ))
                // The traffic lights sit in this row; it also drags the window.
                .child(div().h(z(32.)).flex_none())
                .child(div().flex_1().min_h_0().child(nav)),
        );
    div().flex().flex_1().min_h_0().child(rail).child(
        div()
            .flex_1()
            .min_w_0()
            .h_full()
            .flex()
            .flex_col()
            .child(div().h(z(16.)).flex_none())
            .child(page),
    )
}
```

窗口选项与主窗口相同：玻璃生效时用透明背景，红绿灯位置用 `glass::traffic_light_position(true)`。导航里的分隔线改用 `fills(pal, true).line`。

### 顶部贯穿工具栏的窗口：图片预览

图片由 GPUI 绘制，玻璃不能放在图片上方；能做成玻璃的只有头部工具栏和侧边检查器。

```rust
/// Options for a window whose glass toolbar spans the top edge: the traffic
/// lights are centred on its 48-point header.
fn toolbar_window_options(glass_on: bool) -> WindowOptions {
    WindowOptions {
        titlebar: Some(TitlebarOptions {
            title: Some("Preview".into()),
            appears_transparent: true,
            traffic_light_position: Some(if glass_on {
                glass::toolbar_traffic_light_position(z(48.))
            } else {
                point(z(9.), z(13.))
            }),
        }),
        window_background: if glass_on {
            WindowBackgroundAppearance::Transparent
        } else {
            WindowBackgroundAppearance::Opaque
        },
        ..Default::default()
    }
}

/// The header: capsules after the traffic lights, the last group lined up
/// with the inspector's outer edge, and no title-bar fill.
fn toolbar_header(groups: Vec<(&'static str, Vec<AnyElement>)>, trailing: AnyElement) -> Div {
    div()
        .h(z(48.))
        .flex_none()
        .flex()
        .items_center()
        .gap(z(8.))
        .pl(px(glass::TRAFFIC_END))
        .pr(px(glass::INSET))
        .children(
            groups
                .into_iter()
                .map(|(key, items)| glass::capsule(key, z(34.)).children(items)),
        )
        // The window drag region.
        .child(div().flex_1().h_full())
        .child(trailing)
}

/// Canvas and inspector floating apart on glass. The inspector is a
/// desktop-backed pane inset from the window edges; the divider runs down
/// the middle of the gap and draws only a pill while held.
fn floating_split(canvas: AnyElement, inspector: AnyElement, width: Pixels) -> impl IntoElement {
    let radius = px(glass::SIDEBAR_RADIUS);
    h_resizable("preview-split")
        .with_handle_appearance(glass::floating_divider())
        .child(
            resizable_panel()
                .size(width * 0.45)
                .pr(px(glass::INSET / 2.))
                .child(canvas),
        )
        .child(
            resizable_panel()
                .size(width * 0.55)
                .pl(px(glass::INSET / 2.))
                .pr(px(glass::INSET))
                .pb(px(glass::INSET))
                .child(
                    div()
                        .relative()
                        .size_full()
                        .rounded(radius)
                        .child(glass::pane(
                            "preview-inspector",
                            Pane::new(radius, Backdrop::Desktop),
                        ))
                        .child(inspector),
                ),
        )
}
```

预览窗口在打开时读取一次 `glass::active(cx)` 来决定红绿灯位置。头部按钮仍是 30 的圆形，垂直居中于 48 高的头部。
