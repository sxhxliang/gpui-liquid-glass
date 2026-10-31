# 参考实现

需要可移植的完整代码时读取本页。它来自一个已发布的 GPUI Kit 应用：侧栏、工具栏胶囊、标签轨道、停靠格式栏、设置窗口导航和图片预览检查器都由这套代码驱动，在 macOS 26 及以上的真实窗口中运行，浅色和深色外观经实机截图确认。

本页相对该应用只有三处改动：

- 应用其他模块的引用换成下面的 `host` 契约。
- 原生面板按创建时的后端保存为 `NativePane` 枚举。原代码用 `downcast_ref::<NSGlassEffectView>()` 设置圆角，在 macOS 26 之前会因找不到类而 panic。
- 玻璃容器单独检查 `NSGlassEffectContainerView` 类是否存在。

依赖版本为 `gpui-kit 0.8.1`（含 `gpui-pre 0.3.10`）、`objc2 0.6.5`、`objc2-app-kit 0.3.2`、`objc2-quartz-core 0.3.2`、`objc2-core-graphics 0.3.2`。Cargo features 见 [macos-bridge.md](macos-bridge.md#cargo-features)。三段代码按以上版本在 macOS 上通过了 `cargo check` 和 `cargo clippy -- -D warnings`。旧系统的 `NSVisualEffectView` 路径没有在真实窗口中运行过。

模块布局：

```text
src/host.rs         宿主契约，由应用提供
src/glass.rs        GPUI 侧接口；在 crate 根声明 `mod host; mod glass;`
src/glass/macos.rs  AppKit 侧，仅 macOS 的非测试构建编译
```

调用方式见 [gpui-examples.md](gpui-examples.md)，帧协议见 [gpui-pane-protocol.md](gpui-pane-protocol.md)，控件样式见 [project-controls.md](project-controls.md)。

## 宿主契约

玻璃模块只需要应用提供这些。`Palette` 由应用从实际主题生成，并在每次主题变化时用 `cx.set_global` 更新；颜色为 `0xRRGGBB`。不要在别处再维护一份冲突的主题状态。

```rust
//! What the glass module needs from the host application.

use gpui_kit::*;

/// The active theme's colours, as `0xRRGGBB`.
#[derive(Clone, Copy)]
pub struct Palette {
    pub fg: u32,
    pub bg: u32,
    pub hover: u32,
    pub active: u32,
    pub line: u32,
    pub menu_bg: u32,
    pub menu_border: u32,
}

impl Global for Palette {}

/// Dark palettes: a background luma below one half.
pub fn is_dark(pal: &Palette) -> bool {
    let channel = |shift: u32| ((pal.bg >> shift) & 0xff) as f32 / 255.;
    0.2126 * channel(16) + 0.7152 * channel(8) + 0.0722 * channel(0) < 0.5
}

/// A logical size scaled by the UI zoom setting. Without a zoom setting
/// this is just `px`.
pub fn z(v: f32) -> Pixels {
    px(v)
}

/// An SVG icon tinted `color`.
pub fn icon(path: &'static str, color: u32) -> Svg {
    svg()
        .path(path)
        .size(z(16.))
        .flex_none()
        .text_color(rgb(color))
}
```

应用自己的主题流程在更新 GPUI Kit `Theme` 的 token 之后，最后调用 `glass::adapt_theme(&palette, cx)`。`sync_window` 在窗口根是 GPUI Kit `Root` 时清除它的背景；不用 `Root` 的应用删掉这个分支，并自行清除祖先背景。

## GPUI 模块

```rust
//! Liquid Glass: the system's own glass material behind the window chrome.
//!
//! GPUI draws the whole window into one Metal layer, so it cannot sample or
//! refract what lies beneath an element. On macOS the window is made
//! transparent instead, and real AppKit views are kept *below* GPUI's view:
//!
//! 1. a backdrop layer painted in the palette background, with a cut-out
//!    under every [`Backdrop::Desktop`] pane so the desktop shows through;
//! 2. one `NSGlassEffectView` per [`pane`] (macOS 26+), falling back to an
//!    `NSVisualEffectView` on older systems, all inside a glass container
//!    so neighbouring panes are rendered together;
//! 3. GPUI's view on top, leaving each pane's area unpainted so the glass
//!    shows through and the pane's own controls sit on the glass.
//!
//! Panes record their painted bounds every frame; [`commit`], painted last,
//! reconciles the native views with that frame's list. Elsewhere (other
//! platforms, tests, or the setting turned off) everything here is inert
//! and the chrome renders opaque as before.

use gpui_kit::base::{ResizeHandleContext, ResizeHandleRenderer, ResizeHandleState};
use gpui_kit::component::button::Button;
use gpui_kit::component::tooltip::Tooltip;
use gpui_kit::component::{ActiveTheme as _, Root, Theme};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::*;

use crate::host::{Palette, icon, is_dark, z};

#[cfg(all(target_os = "macos", not(test)))]
mod macos;

/// What a pane refracts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Backdrop {
    /// Whatever is behind the window: the floating sidebar.
    Desktop,
    /// The window background: controls floating over the content.
    Window,
}

/// One glass surface, sized by the element it is painted in.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Pane {
    pub radius: Pixels,
    pub backdrop: Backdrop,
    /// Fade, for panes that animate in and out with their content.
    pub alpha: f32,
}

impl Pane {
    pub(crate) fn new(radius: Pixels, backdrop: Backdrop) -> Self {
        Pane {
            radius,
            backdrop,
            alpha: 1.,
        }
    }
}

/// Whether Liquid Glass is in use: the setting is on and the platform has a
/// glass material. Read by render code to pick the floating layout.
#[derive(Clone, Copy, Default)]
pub(crate) struct Glass(pub bool);

impl Global for Glass {}

/// The platform offers a glass material at all.
pub(crate) fn supported() -> bool {
    #[cfg(all(target_os = "macos", not(test)))]
    {
        macos::supported()
    }
    #[cfg(not(all(target_os = "macos", not(test))))]
    {
        false
    }
}

/// Resolve the setting against the platform and publish it.
pub(crate) fn apply(enabled: bool, cx: &mut App) {
    cx.set_global(Glass(enabled && supported()));
}

pub(crate) fn active(cx: &App) -> bool {
    cx.try_global::<Glass>().is_some_and(|g| g.0)
}

/// Traffic-light origin for new windows: inside the floating sidebar when
/// glass is on, centred on the classic 48-point header otherwise.
pub(crate) fn traffic_light_position(enabled: bool) -> Point<Pixels> {
    if enabled && supported() {
        point(px(TRAFFIC_X), px(TRAFFIC_Y))
    } else {
        point(px(12.), px(18.))
    }
}

/// Traffic-light origin for a window whose glass toolbar spans the top edge
/// instead of riding in a floating sidebar: centred on a bar `height` tall.
pub(crate) fn toolbar_traffic_light_position(height: Pixels) -> Point<Pixels> {
    point(px(TRAFFIC_X), (height - px(TRAFFIC_SIZE)) / 2.)
}

/// Inset of floating panes from the window edge.
pub(crate) const INSET: f32 = 8.;
/// Corner radius of the floating sidebar.
pub(crate) const SIDEBAR_RADIUS: f32 = 16.;
const TRAFFIC_X: f32 = INSET + 12.;
const TRAFFIC_Y: f32 = INSET + 13.;
/// Height of the traffic-light buttons.
const TRAFFIC_SIZE: f32 = 14.;
/// Where content may start to the right of the traffic lights.
pub(crate) const TRAFFIC_END: f32 = TRAFFIC_X + 68.;

/// Match the window to glass: transparent, with the theme background that
/// `Root` paints switched off, while panes are shown; opaque otherwise.
/// Cheap to call on every render.
pub(crate) fn sync_window(window: &mut Window, cx: &mut App) {
    let want = if active(cx) {
        WindowBackgroundAppearance::Transparent
    } else {
        WindowBackgroundAppearance::Opaque
    };
    let id = window.window_handle().window_id();
    let changed = APPLIED.with_borrow_mut(|applied| applied.insert(id, want) != Some(want));
    if !changed {
        return;
    }
    window.set_background_appearance(want);
    if let Some(Some(root)) = window.root::<Root>() {
        let clear = want == WindowBackgroundAppearance::Transparent;
        root.update(cx, |root, cx| {
            root.style().background = clear.then(|| transparent_black().into());
            cx.notify();
        });
    }
}

/// An absolutely positioned child that turns its parent's bounds into a
/// glass pane. The parent must not paint a background of its own.
pub(crate) fn pane(key: &'static str, pane: Pane) -> impl IntoElement {
    canvas(
        |_, _, _| {},
        move |bounds, _, window, _| record(window, key, bounds, pane),
    )
    .absolute()
    .inset_0()
}

/// A group of controls on one glass capsule, the way Tahoe groups toolbar
/// items. Buttons inside should come from [`icon_btn`].
pub(crate) fn capsule(key: &'static str, height: Pixels) -> Div {
    div()
        .relative()
        .flex_none()
        .flex()
        .items_center()
        .h(height)
        .px(height * 0.1)
        .gap(height * 0.05)
        .rounded(height / 2.)
        .child(pane(key, Pane::new(height / 2., Backdrop::Window)))
}

/// An icon button for a glass surface: circular, with translucent hover,
/// press and toggled fills instead of opaque palette squares.
pub(crate) fn icon_btn(
    id: &'static str,
    path: &'static str,
    label: SharedString,
    on: bool,
    pal: &Palette,
) -> Stateful<Div> {
    styled_icon_btn(id, path, label, on, pal, fills(pal, true), z(15.))
}

/// An icon button whose hover and press fills and corner radius are chosen
/// up front. GPUI allows one hover style per element, so the result cannot
/// be restyled afterwards; the opaque variant passes `fills(pal, false)`.
pub(crate) fn styled_icon_btn(
    id: &'static str,
    path: &'static str,
    label: SharedString,
    on: bool,
    pal: &Palette,
    fills: Fills,
    radius: Pixels,
) -> Stateful<Div> {
    let tip = label.clone();
    div()
        .id(id)
        .role(Role::Button)
        .aria_label(label)
        .tooltip(move |window, cx| Tooltip::new(tip.clone()).build(window, cx))
        .size(z(30.))
        .flex_none()
        .flex()
        .items_center()
        .justify_center()
        .rounded(radius)
        .cursor_pointer()
        .occlude()
        .hover(move |s| s.bg(fills.hover))
        .active(move |s| s.bg(fills.active))
        .when(on, move |s| s.bg(fills.active))
        .child(icon(path, pal.fg))
}

/// The divider of a split view whose panes float apart on glass. The gap
/// between them already divides, so it draws no hairline, only a pill while
/// the pointer holds it.
pub(crate) fn floating_divider() -> ResizeHandleRenderer {
    std::rc::Rc::new(
        |handle: &ResizeHandleContext, _: &mut Window, cx: &mut App| {
            let (length, opacity) = match handle.state() {
                ResizeHandleState::Idle => (px(0.), 0.),
                ResizeHandleState::Hovered => (px(20.), 0.35),
                ResizeHandleState::Pressed => (px(28.), 0.6),
                ResizeHandleState::Dragging => (px(44.), 0.9),
            };
            let color = cx.theme().muted_foreground.opacity(opacity);
            let across = matches!(handle.axis(), Axis::Horizontal);
            // The handle's content box is a one-pixel line; the pill is
            // centred on it and overhangs it by a pixel on each side.
            let line = div().flex_none().flex().map(|line| {
                if across {
                    line.w(px(1.)).h_full().items_center()
                } else {
                    line.h(px(1.)).w_full().justify_center()
                }
            });
            Some(
                line.when(length > px(0.), |line| {
                    line.child(div().flex_none().rounded_full().bg(color).map(|pill| {
                        if across {
                            pill.ml(px(-1.)).w(px(3.)).h(length)
                        } else {
                            pill.mt(px(-1.)).h(px(3.)).w(length)
                        }
                    }))
                })
                .into_any_element(),
            )
        },
    )
}

/// Reconcile the native glass with the panes painted this frame. Must be
/// painted after every [`pane`], so it goes last in the root element.
pub(crate) fn commit(bg: u32, dark: bool) -> impl IntoElement {
    canvas(
        |_, _, _| {},
        move |_, _, window, cx| {
            let enabled = active(cx);
            #[cfg(all(target_os = "macos", not(test)))]
            macos::commit(window, enabled, bg, dark);
            #[cfg(not(all(target_os = "macos", not(test))))]
            {
                let _ = (window, enabled, bg, dark);
                FRAME.with_borrow_mut(Vec::clear);
            }
        },
    )
    .absolute()
    .size_0()
}

/// Hover, selection and hairline fills for controls. On glass they are the
/// palette foreground at low opacity so the material reads through, as
/// AppKit's own selection does; off glass they are the opaque palette
/// colours, unchanged.
#[derive(Clone, Copy)]
pub(crate) struct Fills {
    pub hover: Hsla,
    pub active: Hsla,
    pub line: Hsla,
}

pub(crate) fn fills(pal: &Palette, on_glass: bool) -> Fills {
    if !on_glass {
        return Fills {
            hover: rgb(pal.hover).into(),
            active: rgb(pal.active).into(),
            line: rgb(pal.line).into(),
        };
    }
    let fg = Hsla::from(rgb(pal.fg));
    let (hover, active, line) = if is_dark(pal) {
        (0.08, 0.14, 0.10)
    } else {
        (0.05, 0.09, 0.08)
    };
    Fills {
        hover: fg.opacity(hover),
        active: fg.opacity(active),
        line: fg.opacity(line),
    }
}

/// Fit the component library to glass: hover and press tokens become the
/// translucent [`Fills`] (identical in effect on solid surfaces, see-through
/// on glass), push buttons rest on the [`raised`] fill, and radii grow toward
/// Tahoe's rounder controls. Runs after every theme change; with glass off it
/// restores the library's default radii, while the theme change it follows
/// has already rebuilt the colour tokens.
pub(crate) fn adapt_theme(pal: &Palette, cx: &mut App) {
    let on = active(cx);
    Theme::update(cx, |theme| {
        if !on {
            theme.radius = px(6.);
            theme.radius_lg = px(8.);
            return;
        }
        let f = fills(pal, true);
        let fg = Hsla::from(rgb(pal.fg));
        theme.radius = px(10.);
        theme.radius_lg = px(16.);
        theme.accent = f.hover;
        theme.secondary_hover = f.hover;
        theme.secondary_active = f.active;
        theme.button = raised(pal);
        // Between the raised fill and the pressed one.
        theme.button_hover = if is_dark(pal) {
            fg.opacity(0.12)
        } else {
            raised(pal).blend(fg.opacity(0.03))
        };
        theme.button_active = f.active;
        theme.button_secondary_hover = f.hover;
        theme.button_secondary_active = f.active;
    });
}

/// Corner radius GPUI clamps to half the shorter side: a capsule.
const CAPSULE: f32 = 9999.;

/// The fill of a raised control on the solid form of the glass material: the
/// material's own colour, or in dark palettes, where that sits too close to
/// the surfaces below, a lift of the foreground.
pub(crate) fn raised(pal: &Palette) -> Hsla {
    if is_dark(pal) {
        Hsla::from(rgb(pal.fg)).opacity(0.10)
    } else {
        rgb(pal.menu_bg).into()
    }
}

/// What traces a raised control's edge in place of a border: a specular rim
/// inside its top edge, a hairline and a soft contact shadow.
pub(crate) fn control_edge(pal: &Palette) -> Vec<BoxShadow> {
    let (rim, edge, contact) = if is_dark(pal) {
        (0.12, 0.5, 0.35)
    } else {
        (0.45, 0.08, 0.10)
    };
    vec![
        BoxShadow {
            inset: true,
            color: hsla(0., 0., 1., rim),
            offset: point(px(0.), px(1.)),
            blur_radius: px(0.),
            spread_radius: px(0.),
        },
        BoxShadow {
            inset: false,
            color: hsla(0., 0., 0., edge),
            offset: point(px(0.), px(0.)),
            blur_radius: px(0.),
            spread_radius: px(0.5),
        },
        BoxShadow {
            inset: false,
            color: hsla(0., 0., 0., contact),
            offset: point(px(0.), px(1.)),
            blur_radius: px(2.),
            spread_radius: px(0.),
        },
    ]
}

/// Buttons under Liquid Glass take Tahoe's control geometry: capsules. Real
/// glass cannot show beneath controls on the cards and pages GPUI paints, so
/// a push button takes the material's solid form instead: the theme's
/// [`raised`] fill, with [`control_edge`] in place of its border. Off glass
/// these leave a button untouched.
pub(crate) trait GlassButtonExt: Sized {
    /// A button that draws a fill or border: default, primary and the like.
    fn glass_push(self, cx: &App) -> Self;
    /// A borderless button: only its hover and press fills change shape.
    fn glass_plain(self, cx: &App) -> Self;
    /// The selected choice of a segmented control: a raised capsule.
    fn glass_thumb(self, cx: &App) -> Self;
}

impl GlassButtonExt for Button {
    fn glass_push(self, cx: &App) -> Self {
        if !active(cx) {
            return self;
        }
        let edge = control_edge(cx.global::<Palette>());
        // `Button::shadow` toggles its own shadow; the edge replaces it.
        Styled::shadow(
            self.rounded(px(CAPSULE)).border_color(transparent_black()),
            edge,
        )
    }

    fn glass_plain(self, cx: &App) -> Self {
        if active(cx) {
            self.rounded(px(CAPSULE))
        } else {
            self
        }
    }

    fn glass_thumb(self, cx: &App) -> Self {
        if !active(cx) {
            return self;
        }
        let pal = cx.global::<Palette>();
        Styled::shadow(self.rounded(px(CAPSULE)).bg(raised(pal)), control_edge(pal))
    }
}

/// The material for surfaces that float over content inside the window —
/// menus, palettes, popovers and cards.
///
/// System glass can only sit beneath GPUI's layer, never above content GPUI
/// draws, so these cannot refract. Under Liquid Glass they take the form the
/// system itself gives glass when transparency is reduced: a solid surface
/// with glass geometry — a doubled, concentric corner radius, a specular rim
/// along the inside edge, a hairline outer edge and a soft, deep shadow.
/// Off glass they are the classic bordered card at `radius`.
pub(crate) trait SurfaceExt: Styled + Sized {
    fn surface(self, pal: &Palette, radius: Pixels, cx: &App) -> Self {
        if !active(cx) {
            return self
                .bg(rgb(pal.menu_bg))
                .border_1()
                .border_color(rgb(pal.menu_border))
                .rounded(radius)
                .shadow_lg();
        }
        let (rim, edge, depth) = if is_dark(pal) {
            (0.14, 0.55, 0.45)
        } else {
            (0.85, 0.10, 0.14)
        };
        self.bg(rgb(pal.menu_bg)).rounded(radius * 2.).shadow(vec![
            BoxShadow {
                inset: true,
                color: hsla(0., 0., 1., rim),
                offset: point(px(0.), px(0.5)),
                blur_radius: px(0.),
                spread_radius: px(0.5),
            },
            BoxShadow {
                inset: false,
                color: hsla(0., 0., 0., edge),
                offset: point(px(0.), px(0.)),
                blur_radius: px(0.),
                spread_radius: px(0.5),
            },
            BoxShadow {
                inset: false,
                color: hsla(0., 0., 0., depth),
                offset: point(px(0.), px(10.)),
                blur_radius: px(16.),
                spread_radius: px(-4.),
            },
        ])
    }
}

impl<T: Styled + Sized> SurfaceExt for T {}

/// One pane painted this frame, as the native side receives it.
#[cfg_attr(not(all(target_os = "macos", not(test))), allow(dead_code))]
pub(crate) struct Painted {
    pub key: &'static str,
    pub bounds: Bounds<Pixels>,
    pub pane: Pane,
}

thread_local! {
    /// Panes painted since the last commit. GPUI paints one window at a time
    /// on the main thread, so a single list suffices as long as every window
    /// that paints a pane also paints its own [`commit`].
    static FRAME: std::cell::RefCell<Vec<Painted>> = const { std::cell::RefCell::new(Vec::new()) };
    /// Background appearance last requested per window.
    static APPLIED: std::cell::RefCell<std::collections::HashMap<WindowId, WindowBackgroundAppearance>> =
        std::cell::RefCell::new(std::collections::HashMap::new());
}

fn record(_window: &Window, key: &'static str, bounds: Bounds<Pixels>, pane: Pane) {
    if bounds.size.width <= px(0.5) || bounds.size.height <= px(0.5) {
        return;
    }
    FRAME.with_borrow_mut(|frame| frame.push(Painted { key, bounds, pane }));
}

/// Take this frame's panes, leaving the list empty for the next frame.
#[cfg_attr(not(all(target_os = "macos", not(test))), allow(dead_code))]
fn take_frame() -> Vec<Painted> {
    FRAME.with_borrow_mut(std::mem::take)
}
```

## AppKit 子模块

```rust
//! AppKit side of [`super`]: keeps `NSGlassEffectView`s (or, before macOS 26,
//! `NSVisualEffectView`s) and a backdrop layer beneath GPUI's view.

use std::cell::RefCell;
use std::collections::HashMap;

use gpui_kit::{Bounds, Pixels, Window};
use objc2::rc::Retained;
use objc2::runtime::AnyClass;
use objc2::{MainThreadMarker, MainThreadOnly, Message};
use objc2_app_kit::{
    NSAppearance, NSAppearanceCustomization, NSAppearanceNameAqua, NSAppearanceNameDarkAqua,
    NSAutoresizingMaskOptions, NSGlassEffectContainerView, NSGlassEffectView,
    NSGlassEffectViewStyle, NSView, NSVisualEffectBlendingMode, NSVisualEffectMaterial,
    NSVisualEffectState, NSVisualEffectView, NSWindowOrderingMode,
};
use objc2_core_foundation::{CGPoint, CGRect, CGSize};
use objc2_core_graphics::{CGColor, CGMutablePath};
use objc2_quartz_core::{CAShapeLayer, CATransaction, kCAFillRuleEvenOdd};
use raw_window_handle::{HasWindowHandle, RawWindowHandle};

use super::{Backdrop, Painted, take_frame};

pub(super) fn supported() -> bool {
    MainThreadMarker::new().is_some()
}

fn has_liquid_glass() -> bool {
    AnyClass::get(c"NSGlassEffectView").is_some()
}

fn has_glass_container() -> bool {
    AnyClass::get(c"NSGlassEffectContainerView").is_some()
}

/// A pane's material, by the class it was created with. Kept typed so that
/// updates never look up `NSGlassEffectView` on systems that lack it.
enum NativePane {
    Liquid(Retained<NSGlassEffectView>),
    Vibrancy(Retained<NSVisualEffectView>),
}

impl NativePane {
    fn view(&self) -> &NSView {
        match self {
            Self::Liquid(view) => view,
            Self::Vibrancy(view) => view,
        }
    }

    fn set_radius(&self, radius: f64) {
        match self {
            Self::Liquid(view) => view.setCornerRadius(radius),
            Self::Vibrancy(view) => {
                if let Some(layer) = view.layer() {
                    layer.setCornerRadius(radius);
                    layer.setMasksToBounds(true);
                }
            }
        }
    }
}

struct PaneView {
    native: NativePane,
    radius: f64,
    alpha: f32,
}

/// Native views owned for one GPUI window.
struct Native {
    gpui: Retained<NSView>,
    backdrop: Retained<NSView>,
    shape: Retained<CAShapeLayer>,
    /// The glass container (macOS 26+) or a plain view.
    container: Retained<NSView>,
    /// Where pane views live: the container's content view.
    host: Retained<NSView>,
    panes: HashMap<&'static str, PaneView>,
    attached: bool,
    bg: Option<u32>,
    dark: Option<bool>,
    cutouts: Vec<(CGRect, f64)>,
    size: CGSize,
}

thread_local! {
    static WINDOWS: RefCell<HashMap<usize, Native>> = RefCell::new(HashMap::new());
}

fn gpui_view(window: &Window) -> Option<Retained<NSView>> {
    let handle = HasWindowHandle::window_handle(window).ok()?;
    let RawWindowHandle::AppKit(handle) = handle.as_raw() else {
        return None;
    };
    // SAFETY: the handle is GPUI's live NSView for `window`, borrowed on the
    // main thread for the duration of this paint.
    let view = unsafe { &*handle.ns_view.as_ptr().cast::<NSView>() };
    Some(view.retain())
}

pub(super) fn commit(window: &Window, enabled: bool, bg: u32, dark: bool) {
    let frame = take_frame();
    let Some(mtm) = MainThreadMarker::new() else {
        return;
    };
    let Some(gpui) = gpui_view(window) else {
        return;
    };
    let key = Retained::as_ptr(&gpui) as usize;
    WINDOWS.with_borrow_mut(|windows| {
        // Forget windows AppKit has closed.
        windows.retain(|_, native| native.gpui.window().is_some());
        if !enabled {
            if let Some(native) = windows.get_mut(&key) {
                native.detach();
            }
            return;
        }
        let native = windows
            .entry(key)
            .or_insert_with(|| Native::new(gpui.clone(), mtm));
        CATransaction::begin();
        CATransaction::setDisableActions(true);
        native.sync(&frame, bg, dark, mtm);
        CATransaction::commit();
    });
}

impl Native {
    fn new(gpui: Retained<NSView>, mtm: MainThreadMarker) -> Self {
        let zero = CGRect::new(CGPoint::new(0., 0.), CGSize::new(0., 0.));
        let shape = CAShapeLayer::new();
        shape.setFillRule(unsafe { kCAFillRuleEvenOdd });
        // A layer-hosting view: AppKit leaves the layer's contents to us.
        let backdrop = NSView::initWithFrame(NSView::alloc(mtm), zero);
        backdrop.setLayer(Some(&shape));
        backdrop.setWantsLayer(true);
        let (container, host): (Retained<NSView>, Retained<NSView>) =
            if has_liquid_glass() && has_glass_container() {
                let container = NSGlassEffectContainerView::initWithFrame(
                    NSGlassEffectContainerView::alloc(mtm),
                    zero,
                );
                let host = NSView::initWithFrame(NSView::alloc(mtm), zero);
                host.setAutoresizingMask(sizable());
                container.setContentView(Some(&host));
                (container.into_super(), host)
            } else {
                let host = NSView::initWithFrame(NSView::alloc(mtm), zero);
                (host.clone(), host)
            };
        for view in [&backdrop, &container] {
            view.setAutoresizingMask(sizable());
        }
        Native {
            gpui,
            backdrop,
            shape,
            container,
            host,
            panes: HashMap::new(),
            attached: false,
            bg: None,
            dark: None,
            cutouts: Vec::new(),
            size: CGSize::new(0., 0.),
        }
    }

    fn attach(&mut self) {
        if self.attached {
            return;
        }
        let Some(parent) = (unsafe { self.gpui.superview() }) else {
            return;
        };
        let bounds = parent.bounds();
        self.backdrop.setFrame(bounds);
        self.container.setFrame(bounds);
        if !std::ptr::eq(&*self.host, &*self.container) {
            self.host
                .setFrame(CGRect::new(CGPoint::new(0., 0.), bounds.size));
        }
        // Bottom to top: backdrop, glass, GPUI.
        parent.addSubview_positioned_relativeTo(
            &self.container,
            NSWindowOrderingMode::Below,
            Some(&self.gpui),
        );
        parent.addSubview_positioned_relativeTo(
            &self.backdrop,
            NSWindowOrderingMode::Below,
            Some(&self.container),
        );
        self.attached = true;
    }

    fn detach(&mut self) {
        if !self.attached {
            return;
        }
        for (_, pane) in self.panes.drain() {
            pane.native.view().removeFromSuperview();
        }
        self.container.removeFromSuperview();
        self.backdrop.removeFromSuperview();
        self.attached = false;
        self.cutouts.clear();
        self.bg = None;
        self.dark = None;
    }

    fn sync(&mut self, frame: &[Painted], bg: u32, dark: bool, mtm: MainThreadMarker) {
        self.attach();
        if !self.attached {
            return;
        }
        let size = self.backdrop.bounds().size;
        if self.dark != Some(dark) {
            let name = unsafe {
                if dark {
                    NSAppearanceNameDarkAqua
                } else {
                    NSAppearanceNameAqua
                }
            };
            let appearance = NSAppearance::appearanceNamed(name);
            self.container.setAppearance(appearance.as_deref());
            self.dark = Some(dark);
        }
        if self.bg != Some(bg) {
            let channel = |shift: u32| f64::from((bg >> shift) & 0xff) / 255.;
            let color = CGColor::new_srgb(channel(16), channel(8), channel(0), 1.);
            self.shape.setFillColor(Some(&color));
            self.bg = Some(bg);
        }

        let mut cutouts = Vec::new();
        let mut seen = Vec::with_capacity(frame.len());
        for painted in frame {
            let rect = to_appkit(painted.bounds, size.height);
            let radius = f64::from(painted.pane.radius)
                .min(rect.size.height / 2.)
                .min(rect.size.width / 2.);
            if painted.pane.backdrop == Backdrop::Desktop {
                cutouts.push((rect, radius));
            }
            let pane = self.panes.entry(painted.key).or_insert_with(|| {
                let native = new_pane(painted.pane.backdrop, mtm);
                self.host.addSubview(native.view());
                PaneView {
                    native,
                    radius: -1.,
                    alpha: 1.,
                }
            });
            let view = pane.native.view();
            if view.frame() != rect {
                view.setFrame(rect);
            }
            view.setAutoresizingMask(anchoring(rect, size));
            if pane.radius != radius {
                pane.native.set_radius(radius);
                pane.radius = radius;
            }
            if pane.alpha != painted.pane.alpha {
                view.setAlphaValue(f64::from(painted.pane.alpha.clamp(0., 1.)));
                pane.alpha = painted.pane.alpha;
            }
            seen.push(painted.key);
        }
        self.panes.retain(|key, pane| {
            let keep = seen.contains(key);
            if !keep {
                pane.native.view().removeFromSuperview();
            }
            keep
        });

        if cutouts != self.cutouts || size != self.size {
            let path = CGMutablePath::new();
            let whole = CGRect::new(CGPoint::new(0., 0.), size);
            unsafe {
                CGMutablePath::add_rect(Some(&path), std::ptr::null(), whole);
                for (rect, radius) in &cutouts {
                    CGMutablePath::add_rounded_rect(
                        Some(&path),
                        std::ptr::null(),
                        *rect,
                        *radius,
                        *radius,
                    );
                }
            }
            self.shape.setPath(Some(&path));
            self.cutouts = cutouts;
            self.size = size;
        }
    }
}

fn sizable() -> NSAutoresizingMaskOptions {
    NSAutoresizingMaskOptions::ViewWidthSizable | NSAutoresizingMaskOptions::ViewHeightSizable
}

/// Keep a pane glued to its nearest edges while AppKit live-resizes the
/// window, until the next GPUI frame places it exactly.
fn anchoring(rect: CGRect, size: CGSize) -> NSAutoresizingMaskOptions {
    let mut mask = NSAutoresizingMaskOptions::empty();
    mask |= if rect.size.width > size.width * 0.5 {
        NSAutoresizingMaskOptions::ViewWidthSizable
    } else if rect.origin.x + rect.size.width / 2. > size.width / 2. {
        NSAutoresizingMaskOptions::ViewMinXMargin
    } else {
        NSAutoresizingMaskOptions::ViewMaxXMargin
    };
    mask |= if rect.size.height > size.height * 0.5 {
        NSAutoresizingMaskOptions::ViewHeightSizable
    } else {
        // AppKit's y axis points up: a flexible bottom margin pins the top.
        NSAutoresizingMaskOptions::ViewMinYMargin
    };
    mask
}

fn new_pane(backdrop: Backdrop, mtm: MainThreadMarker) -> NativePane {
    let zero = CGRect::new(CGPoint::new(0., 0.), CGSize::new(0., 0.));
    if has_liquid_glass() {
        let glass = NSGlassEffectView::initWithFrame(NSGlassEffectView::alloc(mtm), zero);
        glass.setStyle(NSGlassEffectViewStyle::Regular);
        return NativePane::Liquid(glass);
    }
    let effect = NSVisualEffectView::initWithFrame(NSVisualEffectView::alloc(mtm), zero);
    let (material, blending) = match backdrop {
        Backdrop::Desktop => (
            NSVisualEffectMaterial::Sidebar,
            NSVisualEffectBlendingMode::BehindWindow,
        ),
        Backdrop::Window => (
            NSVisualEffectMaterial::HeaderView,
            NSVisualEffectBlendingMode::WithinWindow,
        ),
    };
    effect.setMaterial(material);
    effect.setBlendingMode(blending);
    effect.setState(NSVisualEffectState::FollowsWindowActiveState);
    effect.setWantsLayer(true);
    NativePane::Vibrancy(effect)
}

/// GPUI bounds (top-left origin, points) to the parent view's frame space.
fn to_appkit(bounds: Bounds<Pixels>, height: f64) -> CGRect {
    let x = f64::from(bounds.origin.x);
    let y = f64::from(bounds.origin.y);
    let w = f64::from(bounds.size.width);
    let h = f64::from(bounds.size.height);
    CGRect::new(CGPoint::new(x, height - y - h), CGSize::new(w, h))
}
```

## 已知缺口

以下问题来自源码审查，参考应用中尚未处理。移植前按产品需要决定是否修正。

| 缺口 | 影响 | 修正方向 |
| --- | --- | --- |
| 本帧列表 `FRAME` 由所有窗口共用，`record` 不区分窗口 | 某个窗口绘制了面板却没有提交时，面板会落到下一个提交的窗口 | 每个窗口都画 `commit`；更稳妥的做法是按 `WindowId` 分开存储 |
| 已关闭窗口的 `Native` 只在之后任一窗口提交时清理；`APPLIED` 从不删除 | 最后一个窗口关闭后，视图会被保留到进程结束 | 在窗口关闭回调中移除两个表里的对应项 |
| 没有复制 GPUI 的滚动裁剪 | 被裁剪的面板，其原生玻璃仍按完整 bounds 显示 | 面板的显隐用滑出代替裁剪，或另行传递可见范围 |
| `alpha` 不影响桌面镂空 | 桌面背景的面板淡出时会露出裸露桌面 | 只给窗口背景的面板做淡入淡出 |
| 容器的 `spacing` 保持默认 0 | 相邻面板不会融合 | 需要融合效果时显式设置并实测 |
| 同一个 key 换背景类型时不会重建视图 | 旧系统降级视图保留创建时的 blending mode | key 与背景类型固定对应，不要复用 |
| 坐标换算假定父视图未翻转，且 GPUI 视图铺满父视图 | GPUI 当前的 macOS 窗口满足；其他宿主视图结构不一定满足 | 按 [macos-bridge.md](macos-bridge.md) 的坐标规则核对 |
| 红绿灯只在创建窗口时定位 | 切换玻璃后要重开窗口才会移动 | 状态变化时调用 `Window::set_traffic_light_position`，见 [gpui-examples.md](gpui-examples.md) |
