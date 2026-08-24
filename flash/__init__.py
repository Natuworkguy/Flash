import sys

import colorama

colorama.just_fix_windows_console()

if sys.stdout.isatty():
    print(
        colorama.ansi.set_title("FLASH CLI"),
        end=""
    )
