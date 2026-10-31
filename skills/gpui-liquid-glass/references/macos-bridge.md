# macOS 原生材质桥接

用于实现或审查 GPUI 与 AppKit 的混合合成。API 示例依据 `objc2 0.6.5`、`objc2-app-kit 0.3.2`、`objc2-core-foundation 0.3.2`；使用前核对项目锁定版本和实际后端。

## 材质选择与可用性

| 能力 | 原生类型 | 使用边界 |
| --- | --- | --- |
| Liquid Glass | `NSGlassEffectView` | macOS 26+，运行时检查类是否存在 |
| 多面板合成 | `NSGlassEffectContainerView` | 检查这个类自己的可用性；面板放在它的 `contentView` 后代中 |
| 传统模糊材质 | `NSVisualEffectView` | 旧系统的视觉降级，按来源选择 blending mode |
| 关闭 | 无材质视图 | 恢复应用原有背景和控件样式 |

`MainThreadMarker::new().is_some()` 只能证明当前线程适合 AppKit 调用，不能证明系统具有 Liquid Glass。`#[cfg(target_os = "macos")]` 也不能证明运行时版本。

对计划调用的每个新类先使用 `AnyClass::get(c"ClassName")`。类缺失时，`ClassType::class()`、`alloc()` 以及 `downcast_ref::<NSGlassEffectView>()` 都可能触发类查找失败；检查只放在构造函数入口不足以保护后续无条件的类型转换。按实际创建的后端保存枚举，避免在旧系统上探测不存在的类型。

下面的片段只示范材质创建和圆角分派；窗口挂载、主题、布局及关闭由宿主负责。

```rust
use objc2::rc::Retained;
use objc2::runtime::AnyClass;
use objc2::{MainThreadMarker, MainThreadOnly};
use objc2_app_kit::{
    NSGlassEffectView, NSGlassEffectViewStyle, NSView,
    NSVisualEffectBlendingMode, NSVisualEffectMaterial,
    NSVisualEffectState, NSVisualEffectView,
};
use objc2_core_foundation::{CGPoint, CGRect, CGSize};

enum NativePane {
    Liquid(Retained<NSGlassEffectView>),
    Vibrancy(Retained<NSVisualEffectView>),
}

fn new_pane(behind_window: bool, mtm: MainThreadMarker) -> NativePane {
    let frame = CGRect::new(CGPoint::new(0., 0.), CGSize::new(0., 0.));
    if AnyClass::get(c"NSGlassEffectView").is_some() {
        let glass = NSGlassEffectView::initWithFrame(NSGlassEffectView::alloc(mtm), frame);
        glass.setStyle(NSGlassEffectViewStyle::Regular);
        NativePane::Liquid(glass)
    } else {
        let effect = NSVisualEffectView::initWithFrame(NSVisualEffectView::alloc(mtm), frame);
        let (material, blending) = if behind_window {
            (NSVisualEffectMaterial::Sidebar, NSVisualEffectBlendingMode::BehindWindow)
        } else {
            (NSVisualEffectMaterial::HeaderView, NSVisualEffectBlendingMode::WithinWindow)
        };
        effect.setMaterial(material);
        effect.setBlendingMode(blending);
        effect.setState(NSVisualEffectState::FollowsWindowActiveState);
        effect.setWantsLayer(true);
        NativePane::Vibrancy(effect)
    }
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
```

`behind_window` 在这个示例中仅决定传统材质的 blending mode。`NSGlassEffectView` 没有同名的 `setBlendingMode` API；它取样的是视图层次中位于它后方的内容。macOS 26+ 实机观察：透明窗口加底色层镂空时，玻璃折射桌面和后方窗口；不镂空时，玻璃取样主题色底色层，呈现平底上的 Tahoe 玻璃（效果较含蓄，但正确）。

## Cargo features

按所用能力启用 features，保留项目已有的依赖写法。下面是 [implementation.md](implementation.md) 中整套桥接所需的最小组合，已按 `objc2 0.6.5`、`objc2-app-kit 0.3.2` 和 GPUI Kit 0.8.1 通过 `cargo check`。版本只用来标明核对依据，不要求替换项目的版本。

```toml
[dependencies]
raw-window-handle = "0.6"

[target.'cfg(target_os = "macos")'.dependencies]
objc2 = "0.6"
objc2-app-kit = { version = "0.3", default-features = false, features = ["NSResponder", "NSView", "NSWindow", "NSGraphics", "NSAppearance", "NSGlassEffectView", "NSVisualEffectView", "objc2-core-foundation", "objc2-quartz-core"] }
objc2-core-foundation = { version = "0.3", default-features = false, features = ["CFCGTypes"] }
objc2-core-graphics = { version = "0.3", default-features = false, features = ["CGColor", "CGPath"] }
objc2-quartz-core = { version = "0.3", default-features = false, features = ["CALayer", "CAShapeLayer", "CATransaction", "objc2-core-foundation", "objc2-core-graphics"] }
```

各 feature 的来由：`NSWindow` 用于 `NSView::window()` 判断窗口是否已关闭；`NSGraphics` 提供 `NSWindowOrderingMode`；`NSAppearance` 提供外观名称和 `NSAppearanceCustomization`；`CALayer::setCornerRadius` 需要 Quartz Core 自己的 `objc2-core-foundation` feature，只开 AppKit 的同名 feature 不够；`CAShapeLayer` 的路径和填充色需要 `objc2-core-graphics`。只用上面单个材质片段时，可以去掉 `NSWindow`、`NSGraphics`、`NSAppearance`、`CAShapeLayer`、`CATransaction` 和 Core Graphics。

## 视图与背景的层次

对整窗单 Metal 表面的后端，一种可行的结构是：

```text
上层：GPUI NSView / Metal 表面
      玻璃区域透明，绘制文字、图标和交互状态
中层：NSGlassEffectContainerView → contentView → 材质面板
下层：原生底色视图 / CAShapeLayer
      主题底色，按需要在桌面玻璃区域镂空
后方：桌面或其他窗口
```

从 GPUI 公开的窗口句柄获得 `RawWindowHandle::AppKit`，检查句柄类型和有效期；原生视图位于 GPUI 视图下面的同一父视图中。获取 GPUI 自有 `NSView` 时保持主线程约束，按窗口生命周期持有 `Retained<NSView>`，并为指针转换写出具体的 `SAFETY` 依据。

实际接入中验证过的细节：

- GPUI 视图的父视图是窗口的 content view，GPUI 视图铺满它。玻璃容器用 `addSubview_positioned_relativeTo(container, Below, Some(gpui))` 插到 GPUI 视图下方，底色视图再用同样的方法插到容器下方。三者都设置宽高 autoresizing。
- 底色层是一个 layer-hosting 视图：先 `setLayer(Some(&shape))`，再 `setWantsLayer(true)`，由应用全权管理图层内容。不要往 AppKit 自己管理的 backing layer 里加子图层。
- GPUI 不能在玻璃下方绘制，也画不出带圆角孔的整窗底色，所以玻璃开启时整窗底色只由这个原生图层提供，GPUI 不再绘制任何窗口背景。
- 外观设在容器上（Aqua 或 Dark Aqua，由调色板背景的亮度决定），底色用主题背景的 sRGB `CGColor`，二者都只在变化时写入。
- 面板视图按 key 复用；每次提交时依据面板靠近哪条边设置 autoresizing，让 live resize 期间的玻璃在下一帧 GPUI 布局前贴住边缘。

开启透明窗口时，核对 `WindowBackgroundAppearance::Transparent` / `window.set_background_appearance(...)` 的当前实现。清除玻璃区域上方所有覆盖背景，包括组件根、应用根、包装层和面板自身。GPUI Kit 的 `Root` 插件会给根表面刷主题背景；`Root` 先应用插件样式、再应用自身样式，所以覆盖 `root.style().background` 即可，关闭玻璃时把它设回 `None`。

如果主内容区仍需不透明，可用底色 `CAShapeLayer` 填充窗口矩形，再加入桌面面板的圆角路径，并设置 `kCAFillRuleEvenOdd`。注意多个孔相交时 even-odd 会把重叠部分重新填满；需要重叠面板时使用几何并集或合适的遮罩。

镂空让后方内容有机会参与合成，但不能单凭镂空断言 `NSGlassEffectView` 正在折射桌面。检查当前 AppKit 层次的实际取样；若采用 `NSVisualEffectView` 的 `BehindWindow`，明确说明这是传统材质降级。

需要应用内正文取样时，正文必须真正处于玻璃后方。把玻璃放在整个 GPUI Metal 表面下面，只能看到那个表面下面的原生内容。调整 `z-index` 或清除控件背景不能改变这一限制。

## 容器、原生内容与外观

- 创建 `NSGlassEffectContainerView` 前检查它自己的类可用性，把玻璃视图放入它的 `contentView` 后代中。
- 容器 `spacing = 0` 的默认值允许批处理，并避免邻近视图产生融合形变；要实现靠近后的融合，设置明确的距离并用实际面板验证。
- `NSGlassEffectView` 只保证自己的 `contentView` 放在玻璃效果内。需要原生内容时使用这个入口，避免依赖任意子视图的未保证层次。
- GPUI 控件作为上方兄弟图层保留自己的交互与文字渲染；系统不会自动把这些控件识别成原生玻璃按钮。应用负责它们的选中、按下、焦点和可读性。
- 从实际主题选择 Aqua / Dark Aqua 或系统外观。材质 appearance 与 GPUI 文字主题应一致；颜色、圆角、间距和高度来自项目的主题及缩放规则。
- `Regular` 与 `Clear` 是不同样式，按内容可读性和产品要求选择。仅在确有着色需求时设置 `tintColor`，不要把主题纯色或人工光晕当成背景取样。

## 几何、每帧同步与生命周期

每个窗口保留自己的面板表、当帧几何及提交状态。稳定 ID 应包含面板的语义身份。若多个窗口共用一个全局帧列表（参考实现即如此），每个绘制面板的窗口都必须在自己的 paint 末尾提交，见 [gpui-pane-protocol.md](gpui-pane-protocol.md#多个窗口)。

从布局后的 bounds 收集几何，可用 `canvas`、prepaint 或框架提供的测量钩子。使用透明且不抢占事件的测量元素。若 GPUI 会缓存视图或复用绘制结果，确认收集和提交仍能获得完整的面板集合，避免只重绘正文就误删原生玻璃。

统一提交需要满足：

- 当帧所有面板已经收集；已有视图按 ID 复用，未出现的面板移除。
- 更新真实 frame、圆角、裁剪、底色孔及外观；几何相同时避免重复写入。
- 原生材质跟随 GPUI 的可见范围，尤其是滚动裁剪、边缘滑出和动画中间态；必要时复制裁剪遮罩。
- 以 `CATransaction::setDisableActions(true)` 抑制位置和遮罩的隐式动画，防止与 GPUI 动画重叠；系统材质自己的响应保留。
- live resize 可使用 AppKit autoresizing 作为下一次 GPUI 帧前的临时约束，精确位置仍来自布局后的几何。
- 窗口关闭时移除视图并释放窗口注册项；禁用时 detach 并恢复背景。不能仅依赖另一个窗口未来提交才清理已经关闭的窗口。

GPUI bounds 通常是左上角原点的逻辑长度，AppKit frame 使用父视图坐标中的 points。先检查父视图是否 flipped 及 GPUI 视图在父视图中的偏移；同一原点、非 flipped 父视图的转换为：

```text
x_native = x_gpui
y_native = parent_height - y_gpui - height_gpui
```

父视图 flipped 时采用其实际坐标方向；有偏移时转换坐标。逻辑长度到 AppKit points 不乘 Retina 的 backing scale，只有明确使用物理像素的纹理/遮罩 API 才转换。圆角限制在宽高的一半以内，窗口控制按钮的位置与应用缩放使用项目自己的平台约定。

## 排查

| 现象 | 做法 |
| --- | --- |
| 窗口已透明、原生视图已挂载且 bounds 正确，但看不到玻璃 | 临时把底色层涂成醒目的红色。看不到红色，说明 GPUI 某一层在整窗刷底色（实际遇到的是 GPUI Kit `Root` 和应用的授权包装层），按背景链逐层清除 |
| 怀疑视图顺序不对 | 临时打印 GPUI 父视图的子视图类名和 frame：自下而上应为底色视图、`NSGlassEffectContainerView`、GPUI 视图 |
| 单窗口截图里玻璃发灰，控件周围有斑块 | 这是 `screencapture -l` 这类单窗口截图的伪影：窗口后方不在图中。不要为它改代码；以真实屏幕上的观察为准。曾为此加过隐藏标题栏背景的私有类处理，确认是伪影后已删除 |
| 旧系统启动即 panic “class … could not be found” | 某处对新类调用了 `class()`、`alloc()` 或 `downcast_ref`；改为按创建时的后端枚举分派 |

排查用的颜色、日志和临时入口在交付前全部删除。

## 权威 API 入口

- [NSGlassEffectView](https://developer.apple.com/documentation/appkit/nsglasseffectview)
- [NSGlassEffectContainerView](https://developer.apple.com/documentation/appkit/nsglasseffectcontainerview)
- [NSVisualEffectView](https://developer.apple.com/documentation/appkit/nsvisualeffectview)
- 对 Rust 签名和 feature gates，搜索锁定依赖中生成的 `NSGlassEffectView.rs`、`NSVisualEffectView.rs`、`NSView.rs`；对背景清除和坐标，查看当前 GPUI 窗口后端及组件根实现。
