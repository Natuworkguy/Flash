---
name: print-a-file
description: Print a file or a made document on the user's Mac, and list its printers
---
- list_printers lists the printers set up on the Mac (from lpstat) and marks the default.
- print sends one file to a printer with lp. Give path (a file on disk) or document (the title of one you made with make_document), not both.
- Options: printer (a name from list_printers; left out, the default), copies (1 to 99), pages (such as 1-3,5), two_sided (true for both sides).
- The user sees what will print and says yes or no first, every time, even in autonomous mode; only UltraAuto skips it. A no means do not print it again.
- path must be absolute and under home, a temp folder or /Volumes; hidden paths (any part starting with a dot) are refused.
- No printer set up, no default and no printer named, an unknown printer, or a missing file each give a clear error: tell the user, do not guess another printer.
- Only on a Mac. Sparks have both tools; sub-agents do not.

Example:
list_printers {}
print {"path": "/Users/me/Documents/ticket.pdf", "copies": 2, "two_sided": true}
print {"document": "Weekly brief", "pages": "1-2"}
