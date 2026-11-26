---
name: check-devices-and-batteries
description: Say what is connected and how charged, the Mac, AirPods and other Bluetooth devices, the Apple Watch
---
- devices is read-only. On a Mac it gives the Mac's battery (or that it has none and is on power), the network it is on, and the Bluetooth devices connected now with their battery where macOS knows it (AirPods give left, right and case).
- The paired Apple Watch sends its battery when Flash is opened on it, so devices says its level and when it was last heard from; an old time means the level may have changed.
- Off a Mac, only the watch is reported.

Example:
devices {}
