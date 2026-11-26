---
name: run-commands
description: Run shell commands and scripts well: long jobs, installs, cross-platform care
---
- Prefer the dedicated tools for files: read, write, edit, glob, grep. Use shell for programs: builds, tests, git, package managers, scripts.
- Give long commands a timeout that fits, and run servers in the background (nohup ... &, or a tool's own background flag) so the turn is not stuck waiting.
- Check what platform you are on with get_os before platform-specific commands. macOS uses BSD tools (sed -i '' ...); Windows uses PowerShell syntax.
- Quote paths with spaces. Do not cd somewhere and assume it sticks between calls; use absolute paths.
- For anything longer than a line or two, write a script file and run it, rather than a long one-liner.
- Install what is missing with the project's own manager (pip, npm, brew, apt) and say what you installed.
- Read a command's output before the next step; an error early on usually explains everything after.
- Ask before anything destructive or outside the project: deleting files, force-pushing, changing system settings.

## Examples

```
get_os {}
```

A long build, with room to finish.
```
shell {"command": "npm run build", "timeout": 600}
```

A server left running in the background.
```
shell {"command": "nohup python -m http.server 8000 > server.log 2>&1 &"}
```
