import sys

from . import interrupt

# From the first moment, so a Ctrl+C while Flash is still loading asks
# for a second press rather than ending it with a traceback.
if interrupt.started_as_cli(sys.argv):
    interrupt.guard()

import colorama

colorama.just_fix_windows_console()

if sys.stdout.isatty():
    print(
        colorama.ansi.set_title("FLASH CLI"),
        end=""
    )
