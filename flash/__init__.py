import sys

from . import interrupt

# From the first moment, so a Ctrl+C while Flash is still loading asks
# for a second press rather than ending it with a traceback.
if interrupt.started_as_cli(sys.argv):
    interrupt.guard()

try:
    import colorama
except ModuleNotFoundError:
    # Not installed yet: flash.__main__ installs what Flash needs.
    colorama = None

if colorama is not None:
    colorama.just_fix_windows_console()

    if sys.stdout.isatty():
        print(
            colorama.ansi.set_title("FLASH CLI"),
            end=""
        )
