# Usage examples

[简体中文](usage.zh-CN.md) · [Installation](../README.md#install)

Run these prompts in Codex or Claude Code with the target GPUI project open. Replace paths and component names with those in your project.

The five examples below use Codex's `$gpui-liquid-glass` prefix. In Claude Code, replace it with `/gpui-liquid-glass`; the rest of each prompt stays the same. For example:

```text
/gpui-liquid-glass Implement native Liquid Glass for the navigation sidebar.
Use the desktop as its backdrop, synchronize pane geometry with resizing,
and verify the older-macOS fallback.
```

## Add a desktop-backed sidebar

```text
$gpui-liquid-glass Implement native Liquid Glass for the navigation sidebar.
Use the desktop behind the window as the backdrop. Keep the document surface readable,
follow the existing theme and zoom rules, and synchronize the native pane with
sidebar resizing and show/hide animations. Provide an older-macOS material fallback
and retain the existing presentation on other platforms. Verify the actual call sites
and report which native-window checks were performed.
```

The result should include a real material view, transparent composition above it, synchronized geometry, and a clearly described background source.

## Group toolbar controls into glass capsules

```text
$gpui-liquid-glass Put the existing toolbar controls into native glass capsules.
Use the window background as the backdrop and keep the existing actions and keybindings.
Preserve hover, pressed, selected, disabled, and keyboard focus states.
If adjacent capsules should merge, choose and validate the container spacing.
```

The result should connect the capsules to native pane registration and verify their interaction states. A capsule over a flat window background does not imply sampling of editor content.

## Repair an older-macOS crash

```text
$gpui-liquid-glass Diagnose and fix the crash when glass is enabled on older macOS.
Trace capability selection, native construction, radius updates, and cleanup.
Check that all uses of newer AppKit classes are protected at runtime.
Add a regression check at the lowest layer that can prove the changed behavior,
and state whether the older-system path was tested on a real window.
```

The result should protect the complete fallback lifecycle, including updates after construction.

## Analyze an existing worktree without changes

```text
$gpui-liquid-glass Analyze the Liquid Glass implementation in /path/to/worktree.
Keep this task read-only. Explain the composition order, actual pane call sites,
background sources, supported systems, layout synchronization, and cleanup.
Separate static findings from native-window behavior that has not been observed.
```

The result should identify the regions that actually use glass and support its conclusions with source locations.

## Evaluate refraction of scrolling editor content

```text
$gpui-liquid-glass Evaluate whether this GPUI backend can render a glass toolbar
that refracts the scrolling editor content behind it. Inspect its rendering surfaces
and available backdrop-sampling APIs. Explain the implementation options and their
constraints before proposing a change to the renderer or native view structure.
```

The result should establish where the background content lives in the composition and whether it is available to the material.
