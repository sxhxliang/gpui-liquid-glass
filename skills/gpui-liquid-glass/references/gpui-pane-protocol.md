# GPUI 面板协议

GPUI 布局结果与 AppKit 玻璃视图之间的同步约定。接口版本为 `gpui-pre 0.3.10` 和 GPUI Kit 0.8.1；GPUI Kit 重导出 GPUI，下文从 `gpui_kit` 导入。完整代码见 [implementation.md](implementation.md)，AppKit 一侧见 [macos-bridge.md](macos-bridge.md)。

## 记录面板

面板是放进父容器的一个绝对定位 canvas。它在 paint 阶段拿到布局后的真实 bounds，推入本帧列表：

```rust
pub(crate) fn pane(key: &'static str, pane: Pane) -> impl IntoElement {
    canvas(
        |_, _, _| {},
        move |bounds, _, window, _| record(window, key, bounds, pane),
    )
    .absolute()
    .inset_0()
}
```

- 父容器 `.relative()`，不绘制背景，尺寸由正常布局决定。圆角写在父容器上，`Pane::radius` 取同一个值。
- key 在同一窗口内唯一，并对应固定的语义身份。同一个 key 不要在桌面背景和窗口背景之间切换，因为旧系统降级视图的 blending mode 在创建时就已确定。
- 宽或高不足半个点的 bounds 直接丢弃，例如折叠中或尚未布局的面板。
- 不要在 render 中推算 bounds：动画中间态和缩放后的尺寸只有 paint 时才准确。
- canvas 没有注册鼠标事件，面板内的 GPUI 控件照常接收点击。

## 统一提交

```rust
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
```

GPUI 按子元素顺序 paint，所以 `commit` 必须是窗口根元素的最后一个子元素，才能看到本帧所有面板。这是布局顺序上的约定，GPUI 没有“整窗绘制结束”的事件。提交时依次：

1. 取走本帧列表。按 key 复用原生视图，更新 frame、圆角和透明度，并移除本帧没有出现的视图。
2. 只用桌面背景的面板重建底色层的镂空路径，再更新外观和底色。
3. 在 `CATransaction` 中禁用隐式动画，让原生视图与 GPUI 动画在同一帧到位。

面板不能放进 `deferred(..)` 或 `Root` 的顶层浮层：这些内容在窗口根元素之后才 paint，会错过本帧提交。它们通常浮在正文之上，本来也只能用实体形态。

## 多个窗口

参考实现的本帧列表是一个线程局部的全局列表。GPUI 在主线程上逐个窗口 paint，所以只要每个窗口画完自己的面板就立即提交，一个列表就够用。由此得到几条约束：

- 每个绘制面板的窗口，例如主窗口、设置和图片预览，都要在 render 开头调用 `sync_window`，并把 `commit` 放在自己根元素的最后。漏掉提交的窗口会把面板留给下一个提交的窗口，玻璃出现在错误的窗口里。
- 玻璃关闭时也要渲染 `commit`。它负责拆除原生视图；否则在窗口打开期间关闭玻璃，原生视图会留在原处。
- 测试或某些入口不会安装主题全局。取色用 `cx.try_global::<Palette>()`，取不到时不渲染 `commit`。直接调用 `cx.global()` 会在测试中 panic。
- 若要彻底消除跨窗口串位，可以按 `WindowId` 分开存储本帧列表。参考实现的 `record` 没有使用 window 参数。

## 窗口透明状态

`sync_window` 可以在每次 render 时调用。它按 `WindowId` 缓存上次请求的背景外观，只在变化时调用 `window.set_background_appearance(..)`，因为 GPUI 没有对应的 getter；同时打开或关闭 GPUI Kit `Root` 的背景覆盖。其他需要清除背景的层见 [gpui-examples.md](gpui-examples.md#背景链)。

## 动画、裁剪与缓存

- **裁剪不会传递。** 原生视图按完整 bounds 摆放，GPUI 的 `overflow_hidden` 和滚动裁剪对它无效。面板的显隐改为整体滑动：外层做宽度动画，面板贴住一侧被推出窗口，而不是被裁窄。确实需要裁剪时，另行把可见范围传给原生侧。
- **透明度要显式传递。** GPUI 的元素透明度 `element_opacity` 是 crate 私有的，canvas 读不到祖先的 `.opacity()`。在 `Pane` 上携带 `alpha`，提交时调用 `setAlphaValue`。透明度接近 0 时停止注册，下一次提交就会移除视图。
- **淡出不会缩小镂空。** 桌面背景面板的镂空孔不随 alpha 变化，淡出时会露出裸露的桌面。需要淡入淡出的面板优先使用窗口背景。
- **缓存绘制会漏帧。** 被 GPUI 缓存而跳过 paint 的视图不会注册其中的面板，提交会把它们当作已经消失。玻璃面板所在的视图不要使用缓存绘制；否则改为持久注册并显式移除。

## 自行实现时的通用测量元素

需要把几何交给其他桥接，而不使用全局列表时，可以用这个测量元素：

```rust
use gpui_kit::*;

#[derive(Clone, Copy)]
pub struct PaneGeometry {
    pub bounds: Bounds<Pixels>,
    pub radius: Pixels,
    pub alpha: f32,
}

pub fn measure_pane(
    radius: Pixels,
    alpha: f32,
    record: impl FnOnce(PaneGeometry, &mut Window, &mut App) + 'static,
) -> impl IntoElement {
    canvas(
        |_, _, _| (),
        move |bounds, (), window, cx| {
            if bounds.size.width <= px(0.5) || bounds.size.height <= px(0.5) {
                return;
            }
            record(
                PaneGeometry {
                    bounds,
                    radius: radius
                        .min(bounds.size.width / 2.)
                        .min(bounds.size.height / 2.),
                    alpha: alpha.clamp(0., 1.),
                },
                window,
                cx,
            );
        },
    )
    .absolute()
    .inset_0()
}
```

canvas 的第一个闭包在 prepaint 执行，返回值传给 paint 闭包，两者都是 `FnOnce`。每次构建元素时都提供新的闭包。闭包捕获面板的稳定 ID 和桥接状态；需要区分窗口时，在回调中用 `window.window_handle().window_id()`。
