# 控件与弹层的玻璃造型

真玻璃只能出现在外框下方。按钮、分段控件、菜单和弹层大多画在 GPUI 的卡片或正文上，下面没有材质。它们采用 Tahoe 的几何和玻璃的实体形态，与外框保持一致，但不能折射内容。辅助接口（`fills`、`adapt_theme`、`GlassButtonExt`、`SurfaceExt`、`icon_btn`）的完整代码见 [implementation.md](implementation.md)。

文中数值是参考应用的取值。页内代码块按顺序拼起来就是一个模块，已与参考实现一起通过 `cargo check` 和 `cargo clippy`。

```rust
use gpui_kit::component::button::{Button, ButtonVariants};
use gpui_kit::component::{ActiveTheme as _, Disableable, Selectable, Sizable};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::*;

use crate::glass::{self, GlassButtonExt, SurfaceExt};
use crate::host::{Palette, icon, z};
```

## 三条规则

- **状态色半透明。** 悬停、按下、选中和分隔线改用前景色的低透明度（`glass::fills`），不要用实色方块。在玻璃上它们透出材质；在实色表面上，效果与原来的不透明色相近。
- **填充走主题 token。** 组件库控件的填充通过 GPUI Kit 主题 token 调整；单个实例只改形状和边缘。原因见下面的“GPUI Kit Button 的坑”。
- **几何向 Tahoe 靠拢。** 按钮和分组用胶囊；浮层的圆角加倍；嵌套圆角保持同心：内层圆角 = 外层圆角 − 内边距。

## 状态色

| 用途 | 浅色 | 深色 |
| --- | --- | --- |
| 悬停 `hover` | 前景 5% | 前景 8% |
| 按下、选中 `active` | 前景 9% | 前景 14% |
| 分隔线 `line` | 前景 8% | 前景 10% |

玻璃未生效时，`fills(pal, false)` 原样返回调色板的不透明颜色，所以调用方可以统一写 `glass::fills(&pal, glass::active(cx))`。侧栏的行、搜索结果、空间菜单、链接补全、查找栏等列表都用它。

## 主题 token

`glass::adapt_theme` 在每次主题应用的末尾执行：

| token | 玻璃下 | 关闭时 |
| --- | --- | --- |
| `radius` / `radius_lg` | 10 / 16 | 6 / 8 |
| `accent`、`secondary_hover`、`button_secondary_hover` | `fills.hover` | 由主题重建 |
| `secondary_active`、`button_active`、`button_secondary_active` | `fills.active` | 由主题重建 |
| `button` | `raised`：浅色为菜单底色，深色为前景 10% | 由主题重建 |
| `button_hover` | 深色为前景 12%；浅色为 `raised` 叠加前景 3% | 由主题重建 |

关闭分支只写回圆角，颜色 token 依赖紧邻其前的主题重建。所以开关玻璃时要重新执行完整的主题流程；单独调用 `adapt_theme` 无法撤销玻璃的颜色。两个分支都显式写入圆角，因为主题重载可能重置这些字段。

## GPUI Kit Button 的坑

这些都在 GPUI Kit 0.8.1 的源码中核对过：

- **实例样式会在 selected/disabled 中重放。** `Button` 先绘制变体颜色，再叠加调用方的实例样式，并在 selected 和 disabled 状态里再叠加一次。因此按钮上的 `.bg()` 会让选中和禁用看起来与普通状态一样。改填充要走 token；只在 `.when(selected, ..)` 分支里设置实例 `.bg()`。
- **不要对 Button 调用 `.hover()` 或 `.active()`。** 组件的 render 已经设置过；GPUI 只允许设置一次，第二次触发调试断言 “hover style already set”，调试构建和测试中会 panic。
- **同名的固有方法。** `Button::shadow(bool)` 是开关组件自带阴影，`Button::rounded(impl Into<ButtonRounded>)` 遮住了 `Styled::rounded`。阴影列表要写成 `Styled::shadow(button, edge)`；`rounded(px(9999.))` 经 `From<Pixels>` 得到胶囊，GPUI 会把圆角钳制到短边的一半。
- **边框色与输入框共用 token。** 默认按钮的边框读的是输入框的边框色，全局修改会连带改变输入框。玻璃下只在按钮实例上把边框设为透明，用 `control_edge`（内缘高光、细外缘、接触阴影）代替。

`GlassButtonExt` 把这些规则收拢成三个方法：

| 方法 | 用于 | 效果 |
| --- | --- | --- |
| `glass_push` | 有填充或边框的按钮：默认、主按钮、下拉触发器 | 胶囊，边框透明，加 `control_edge` |
| `glass_plain` | 无边框按钮：ghost、未选中的分段选项 | 只改成胶囊，悬停和按下填充沿用 token |
| `glass_thumb` | 分段控件的选中项 | 胶囊，`raised` 填充加 `control_edge` |

玻璃未生效时三个方法都原样返回按钮。

## 图标按钮

自绘的 `Stateful<Div>` 图标按钮必须在构造时拿到状态色。返回值已经设置过 hover 和 active，调用方不能再补。

```rust
/// A toolbar icon button. Both variants come from one constructor that
/// takes the state fills, rather than restyling a finished button.
fn tool_button(
    id: &'static str,
    path: &'static str,
    label: SharedString,
    on: bool,
    pal: &Palette,
    cx: &App,
) -> Stateful<Div> {
    if glass::active(cx) {
        glass::icon_btn(id, path, label, on, pal)
    } else {
        glass::styled_icon_btn(id, path, label, on, pal, glass::fills(pal, false), z(6.))
    }
}
```

## 设置页控件

所有按钮类控件统一为一种尺寸，即组件库的 small 按钮，与输入框同高（24）。下拉触发器、主按钮和普通按钮都用它；分段控件整体也是 24 高，选项 20 高，嵌在轨道里。开关统一用组件库的 small 开关。

```rust
/// Height of every control on a settings page: the library's small button
/// and input, and the outside of a [`segmented`] control.
const CONTROL_HEIGHT: f32 = 24.;

/// The trigger of a drop-down: the current value and a caret, shaped like
/// every other button on the page.
fn select_button(id: impl Into<ElementId>, label: impl Into<SharedString>, cx: &App) -> Button {
    Button::new(id)
        .small()
        .label(label)
        .dropdown_caret(true)
        .glass_push(cx)
}

/// The one commit action of a form, such as Save.
fn primary_button(id: impl Into<ElementId>, label: impl Into<SharedString>, cx: &App) -> Button {
    Button::new(id)
        .small()
        .primary()
        .label(label)
        .glass_push(cx)
}

/// One choice of a segmented control. It stays a `Button`, keeping the
/// selected, disabled and keyboard behaviour; only the selected branch sets
/// an instance fill, since the button replays instance styles in each state.
fn choice_button(
    id: impl Into<ElementId>,
    label: impl Into<SharedString>,
    selected: bool,
    disabled: bool,
    cx: &App,
) -> Button {
    Button::new(id)
        .small()
        .ghost()
        .h(z(CONTROL_HEIGHT - 4.))
        .label(label)
        .selected(selected)
        .disabled(disabled)
        .map(|button| {
            if !glass::active(cx) {
                button.when(selected, |button| button.bg(cx.theme().secondary_active))
            } else if selected {
                button.glass_thumb(cx)
            } else {
                button.glass_plain(cx)
            }
        })
}

/// The track that groups the choices, as tall as a button. On glass it is a
/// translucent capsule with the selected choice riding on it as a thumb.
fn segmented(pal: &Palette, cx: &App) -> Div {
    let track = div().flex().gap(z(2.));
    if glass::active(cx) {
        track
            .p(z(2.))
            .rounded(z(CONTROL_HEIGHT / 2.))
            .bg(glass::fills(pal, true).hover)
    } else {
        track
            .p(z(1.))
            .rounded(z(8.))
            .border_1()
            .border_color(rgb(pal.line))
            .bg(rgb(pal.bg))
    }
}
```

用一个测试把这些高度固定下来：分别开启和关闭玻璃，逐页检查按钮、下拉和分段选项的高度（见 [verification.md](verification.md)）。

## 浮层：玻璃的实体形态

菜单、搜索面板、查找栏、补全列表、状态卡、更新卡、引导卡和批注都浮在 GPUI 绘制的内容之上，统一用 `SurfaceExt::surface(pal, radius, cx)`：

| 部分 | 玻璃下 | 关闭时 |
| --- | --- | --- |
| 底色 | 菜单底色，不透明 | 菜单底色 |
| 圆角 | `radius × 2` | `radius` |
| 边缘 | 内缘白色高光（浅色 85%，深色 14%，偏移 0.5，扩散 0.5），黑色细外缘（浅色 10%，深色 55%，扩散 0.5） | 1 px 菜单边框 |
| 阴影 | 黑色（浅色 14%，深色 45%），y 偏移 10，模糊 16，扩散 −4 | `shadow_lg` |

这就是系统在“减少透明度”时给玻璃的样子。不要为了“更像玻璃”把这些浮层做成半透明：底下是正文，透出来只会降低可读性。浮层内列表的选中和悬停用 `fills`，行的圆角与卡片同心。

## 搜索：Spotlight 形态

```rust
/// Note search in Spotlight's form: a capsule search bar and, 8 points
/// below it, a card of results. Both float over the editor's text, so they
/// take the solid surface, not native glass.
fn spotlight(pal: &Palette, input: AnyElement, rows: Vec<AnyElement>, cx: &App) -> Div {
    div()
        .absolute()
        .top(z(80.))
        .left_1_2()
        .ml(z(-280.))
        .w(z(560.))
        .flex()
        .flex_col()
        .gap(z(8.))
        .child(
            div()
                .h(z(48.))
                .px(z(16.))
                .flex()
                .items_center()
                .gap(z(10.))
                // `surface` doubles 12 to 24: half the bar's height.
                .surface(pal, z(12.), cx)
                .child(icon("icons/search.svg", pal.fg).size(z(18.)))
                .child(div().flex_1().min_w_0().child(input)),
        )
        .child(
            div()
                .p(z(6.))
                // A card radius of 20 holds rows of 20 - 6 = 14.
                .surface(pal, z(10.), cx)
                .children(rows),
        )
}

/// A result row, concentric with the results card. A selected row keeps
/// its fill under the pointer; the others show the hover fill.
fn result_row(ix: usize, selected: bool, pal: &Palette, content: AnyElement) -> Stateful<Div> {
    let fills = glass::fills(pal, true);
    div()
        .id(("hit", ix))
        .px(z(10.))
        .py(z(6.))
        .rounded(z(14.))
        .cursor_pointer()
        .when(selected, |row| row.bg(fills.active))
        .when(!selected, |row| row.hover(|s| s.bg(fills.hover)))
        .active(|s| s.bg(fills.active))
        .child(content)
}
```

输入框去掉自身的外观和边框，字号放大到 18；原来搜索框与结果之间的分隔线去掉。键盘上下选择、回车打开、Esc 或点击外部关闭等行为保持不变。玻璃关闭时仍是原来的单张卡片。
