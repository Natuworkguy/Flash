"""High stakes: the commands Careful mode still asks about.

Careful sits between Ask first and Auto. Commands and edits run without
asking, except the ones that cannot easily be taken back or reach past
this computer: deleting many files, rewriting git history or pushing it,
running as root, wiping disks, piping a download into a shell, killing
everything, dropping a database, publishing a package, deploying.
"""

from __future__ import annotations

import re
import shlex

# (pattern, why) pairs, matched against each part of a command line.
_RULES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(p, re.I), why) for p, why in (
        (r"\brm\s+(-[a-z]*\s+)*-[a-z]*[rf][a-z]*\b", "deletes files for good"),
        (r"\b(rmdir|shred|srm)\b", "deletes files for good"),
        (r"\bfind\b.*\s-delete\b", "deletes every file it finds"),
        (r"\bgit\s+push\b", "publishes commits"),
        (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|"
         r"restore\s+(--source|\.)|filter-(branch|repo)|rebase|"
         r"branch\s+-D|stash\s+(drop|clear)|reflog\s+expire)",
         "throws work away or rewrites history"),
        (r"\bsudo\b|\bdoas\b|\bsu\s+-?\s*\w*$", "runs as root"),
        (r"\b(mkfs|fdisk|diskutil\s+(erase|partition)|dd\s+.*of=/dev/)",
         "wipes a disk"),
        (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b",
         "runs a downloaded script"),
        (r"\bchmod\s+(-R\s+)?[0-7]*7[0-7]{0,2}\s+/|\bchown\s+-R\s+\S+\s+/",
         "changes permissions across the system"),
        (r"\b(killall|pkill)\b|\bkill\s+-9\s+-1\b", "stops programs"),
        (r"\b(shutdown|reboot|halt|poweroff)\b", "turns the computer off"),
        (r"\bdrop\s+(table|database|schema)\b|\btruncate\s+table\b",
         "deletes a database's data"),
        (r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|"
         r"\bcargo\s+publish\b|\bgem\s+push\b|\bpoetry\s+publish\b",
         "publishes a package"),
        (r"\b(kubectl\s+(delete|apply)|terraform\s+(apply|destroy)|"
         r"vercel\s+--prod|fly\s+deploy|heroku\s+\S*deploy|"
         r"gcloud\s+\S+\s+deploy|aws\s+\S+\s+(delete|terminate))",
         "changes live infrastructure"),
        (r"\b(docker|podman)\s+(system\s+prune|volume\s+rm|rm\s+-f)",
         "deletes containers or their data"),
        (r">\s*/dev/sd|>\s*/etc/|>\s*~/\.(ssh|bashrc|zshrc|profile)",
         "overwrites a system or login file"),
        (r"\b(crontab\s+-r|launchctl\s+(unload|remove|bootout))",
         "removes scheduled jobs or services"),
        (r"\bgh\s+(repo\s+delete|release\s+(create|delete)|pr\s+merge)",
         "changes things on GitHub"),
    ))


def why(command: str) -> str:
    """Why COMMAND is high stakes, or "" when it is not."""

    text = (command or "").strip()
    if not text:
        return ""
    for part in re.split(r"&&|\|\||;|\n", text):
        part = part.strip()
        for pattern, reason in _RULES:
            if pattern.search(part):
                return reason
    # rm of a lot at once, or of a home or root folder.
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        words = text.split()
    if words[:1] == ["rm"]:
        targets = [w for w in words[1:] if not w.startswith("-")]
        if len(targets) > 5 or any(t in ("/", "~", "*", ".", "..")
                                   or t.startswith(("/", "~/")) and
                                   t.count("/") <= 2 for t in targets):
            return "deletes files for good"
    return ""


def high(command: str) -> bool:
    return bool(why(command))
