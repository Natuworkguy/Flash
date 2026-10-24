"""The glow around the screen's edges while Flash has the computer
(Computer Use): a soft, slowly breathing line in Flash's colour, from
the user's yes until the reply lets go, so it is plain whose mouse it
is. Clicks go through it.

It is drawn by a small process of its own: AppKit through JavaScript
for Automation on a Mac, WPF through PowerShell on Windows, and four
thin Tk windows on Linux. It steps out of every screenshot (the model
never sees it), and it goes away by itself if Flash does.
"""

import contextlib
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from .theme import ACCENT

# How often the glow looks for the hide file, and so how long a
# screenshot waits for it to be gone.
LOOK_EVERY = 0.04
HIDE_WAIT = 0.12

_lock = threading.RLock()
_glow: dict = {}


def _rgb() -> str:
    color = ACCENT.lstrip("#")
    return ",".join(str(int(color[i:i + 2], 16)) for i in (0, 2, 4))


def shown() -> bool:
    with _lock:
        process = _glow.get("process")
        return process is not None and process.poll() is None


def show(kind: str) -> None:
    """Glow around the screen, on KIND ("mac", "windows" or "linux"),
    unless it already does. Never raises: it is only a sign."""

    with _lock:
        if shown():
            return
        folder = Path(tempfile.mkdtemp(prefix="flash-glow-"))
        with contextlib.suppress(Exception):
            process = _launch(kind, folder)
            if process is not None:
                _glow.update(process=process, folder=folder)
                return
        shutil.rmtree(folder, ignore_errors=True)


def hide() -> None:
    """Take the glow away: the reply let go of the computer."""

    with _lock:
        process = _glow.pop("process", None)
        folder = _glow.pop("folder", None)
    if process is not None and process.poll() is None:
        with contextlib.suppress(OSError):
            process.kill()
    if folder is not None:
        shutil.rmtree(folder, ignore_errors=True)


@contextlib.contextmanager
def out_of_shot() -> Iterator[None]:
    """Hold the glow off the screen while a screenshot is taken."""

    with _lock:
        folder = _glow.get("folder") if shown() else None
    if folder is None:
        yield
        return
    flag = folder / "hide"
    with contextlib.suppress(OSError):
        flag.touch()
        time.sleep(HIDE_WAIT)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            flag.unlink()


def _launch(kind: str, folder: Path) -> Optional[subprocess.Popen]:
    flag = str(folder / "hide")
    parent = str(os.getpid())
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
             "stderr": subprocess.DEVNULL}
    if kind == "mac":
        if not shutil.which("osascript"):
            return None
        script = folder / "glow.js"
        script.write_text(_MAC_GLOW, encoding="utf-8")
        return subprocess.Popen(  # nosec B603
            ["osascript", "-l", "JavaScript", str(script), parent, flag,
             _rgb()], **quiet)
    if kind == "windows":
        if not shutil.which("powershell"):
            return None
        script = folder / "glow.ps1"
        script.write_text(_WIN_GLOW, encoding="utf-8")
        return subprocess.Popen(  # nosec B603
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy",
             "Bypass", "-File", str(script), parent, flag, _rgb()],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            **quiet)
    if not os.environ.get("DISPLAY"):
        return None
    return subprocess.Popen(  # nosec B603
        [sys.executable, "-c", _LINUX_GLOW, parent, flag, ACCENT], **quiet)


# A Mac: one borderless window over the main screen, clear but for a
# line along its edges with a soft glow, breathing; clicks go through,
# it is on every Space, and nothing takes focus. It hides while the
# hide file is there, and quits when Flash has.
_MAC_GLOW = r"""
ObjC.import('Cocoa');
ObjC.import('QuartzCore');
ObjC.import('stdlib');
function run(argv) {
  const parent = argv[0], flag = argv[1];
  const [r, g, b] = argv[2].split(',').map(Number);
  const app = $.NSApplication.sharedApplication;
  app.setActivationPolicy(1);
  const frame = $.NSScreen.screens.objectAtIndex(0).frame;
  const win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    frame, 0, 2, false);
  win.opaque = false;
  win.backgroundColor = $.NSColor.clearColor;
  win.ignoresMouseEvents = true;
  win.hasShadow = false;
  win.level = 1000;
  win.collectionBehavior = 1 | 16 | 256;
  const view = win.contentView;
  view.wantsLayer = true;
  const color = $.NSColor.colorWithSRGBRedGreenBlueAlpha(
    r / 255, g / 255, b / 255, 1).CGColor;
  const edge = $.CALayer.layer;
  edge.frame = view.bounds;
  edge.borderWidth = 3;
  edge.borderColor = color;
  edge.shadowColor = color;
  edge.shadowOpacity = 1;
  edge.shadowRadius = 18;
  edge.shadowOffset = {width: 0, height: 0};
  view.layer.addSublayer(edge);
  const pulse = $.CABasicAnimation.animationWithKeyPath('opacity');
  pulse.fromValue = $.NSNumber.numberWithDouble(1);
  pulse.toValue = $.NSNumber.numberWithDouble(0.45);
  pulse.duration = 1.4;
  pulse.autoreverses = true;
  pulse.repeatCount = 1e9;
  edge.addAnimationForKey(pulse, 'breathe');
  win.orderFrontRegardless;
  const files = $.NSFileManager.defaultManager;
  let up = true, ticks = 0;
  $.NSTimer.scheduledTimerWithTimeIntervalRepeatsBlock(0.04, true, () => {
    const hidden = files.fileExistsAtPath(flag);
    if (hidden === up) {
      if (hidden) win.orderOut(null); else win.orderFrontRegardless;
      up = !hidden;
    }
    if (++ticks % 25 === 0 &&
        $.system('kill -0 ' + parent + ' 2>/dev/null') !== 0) $.exit(0);
  });
  app.run;
}
"""

# Windows: a WPF window over the primary screen, transparent but for a
# glowing border, breathing; made click-through, out of the taskbar and
# never focused. Clear while the hide file is there; gone with Flash.
_WIN_GLOW = r"""
using namespace System.Windows
using namespace System.Windows.Media
$parent = [int]$args[0]; $flag = $args[1]; $rgb = $args[2] -split ','
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
Add-Type -Namespace FlashGlow -Name User32 -MemberDefinition @'
[DllImport("user32.dll")]
public static extern int GetWindowLong(IntPtr h, int i);
[DllImport("user32.dll")]
public static extern int SetWindowLong(IntPtr h, int i, int v);
'@
$color = [Color]::FromRgb([byte]$rgb[0], [byte]$rgb[1], [byte]$rgb[2])
$w = New-Object Window
$w.WindowStyle = 'None'
$w.AllowsTransparency = $true
$w.Background = [Brushes]::Transparent
$w.Topmost = $true
$w.ShowInTaskbar = $false
$w.ShowActivated = $false
$w.ResizeMode = 'NoResize'
$w.Left = 0
$w.Top = 0
$w.Width = [SystemParameters]::PrimaryScreenWidth
$w.Height = [SystemParameters]::PrimaryScreenHeight
$edge = New-Object Controls.Border
$edge.BorderBrush = New-Object SolidColorBrush $color
$edge.BorderThickness = 3
$glow = New-Object Effects.DropShadowEffect
$glow.Color = $color
$glow.BlurRadius = 30
$glow.ShadowDepth = 0
$edge.Effect = $glow
$w.Content = $edge
$w.Add_SourceInitialized({
  $h = (New-Object Interop.WindowInteropHelper $w).Handle
  $style = [FlashGlow.User32]::GetWindowLong($h, -20)
  # Click-through, out of Alt+Tab, layered, never focused.
  $style = $style -bor 0x20 -bor 0x80 -bor 0x80000 -bor 0x08000000
  [void][FlashGlow.User32]::SetWindowLong($h, -20, $style)
})
$breath = New-Object Duration ([TimeSpan]::FromSeconds(1.4))
$pulse = New-Object Animation.DoubleAnimation(1.0, 0.45, $breath)
$pulse.AutoReverse = $true
$pulse.RepeatBehavior = [Animation.RepeatBehavior]::Forever
$edge.BeginAnimation([UIElement]::OpacityProperty, $pulse)
$script:ticks = 0
$timer = New-Object Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(40)
$timer.Add_Tick({
  if (Test-Path -LiteralPath $flag) { $w.Opacity = 0 }
  else { $w.Opacity = 1 }
  $script:ticks++
  $gone = -not (Get-Process -Id $parent -ErrorAction SilentlyContinue)
  if ($script:ticks % 25 -eq 0 -and $gone) { [Environment]::Exit(0) }
})
$timer.Start()
$w.Show()
[Threading.Dispatcher]::Run()
"""

# Linux: four thin strips along the screen's edges, on top, breathing
# where the window manager lets them be see-through. Withdrawn while
# the hide file is there; gone when Flash is.
_LINUX_GLOW = r"""
import math, os, sys, time
import tkinter as tk
parent, flag, color = int(sys.argv[1]), sys.argv[2], sys.argv[3]
root = tk.Tk()
root.withdraw()
w, h, t = root.winfo_screenwidth(), root.winfo_screenheight(), 4
strips = []
for x, y, sw, sh in ((0, 0, w, t), (0, h - t, w, t), (0, 0, t, h),
                     (w - t, 0, t, h)):
    strip = tk.Toplevel(root)
    strip.overrideredirect(True)
    strip.geometry(f"{sw}x{sh}+{x}+{y}")
    strip.configure(bg=color)
    strip.attributes("-topmost", True)
    strips.append(strip)
up = [True]
def tick():
    if os.getppid() != parent:
        root.destroy()
        return
    hidden = os.path.exists(flag)
    if hidden == up[0]:
        for strip in strips:
            strip.withdraw() if hidden else strip.deiconify()
        up[0] = not hidden
    if not hidden:
        level = 0.725 + 0.275 * math.cos(time.monotonic() * math.pi / 1.4)
        for strip in strips:
            try:
                strip.attributes("-alpha", level)
            except tk.TclError:
                pass
            strip.lift()
    root.after(40, tick)
root.after(40, tick)
root.mainloop()
"""
