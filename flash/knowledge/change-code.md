---
name: change-code
description: Change code in a project: find the right place, edit it safely, test it
---
1. Find before you read: glob for files by name, grep for symbols and strings. search_files when you know what something does but not what it is called.
2. read the files you will change, and enough around them to follow the conventions: naming, error handling, comment style, test layout.
3. Change with edit (one block) or multi_edit (several in one file). Copy old_string from read's output, without the line numbers. Use write only for new files.
4. Match the surrounding code. Change only what the task needs; do not reformat or refactor unrelated lines.
5. Run the project's own checks with verify: it finds its tests, linter and type checker (pyproject.toml, package.json, Cargo.toml, go.mod, Makefile) and runs them. Give commands for a narrower test first when the suite is slow.
6. When verify fails, start from the failing tests and the files and lines it points at: trace the cause, fix it, and verify again until it passes. Say plainly if something still fails, and why it is not from this change.
7. End by saying which checks ran and passed. A change is done when its checks pass. If you finish without checking, you are sent back once to verify.
8. open_in_editor to show the user a spot in VS Code when they ask where something is.

Pitfalls:
- An edit whose old_string appears twice fails: include more surrounding lines.
- Never delete or skip a failing test to get green.
- For a task with several steps, post a plan first and tick steps as you go.
- Before calling a big change done, run review_code on it and fix what it finds.

## Examples

```
grep {"pattern": "def parse_date", "path": "src"}
```

```
read {"path": "src/dates.py", "offset": 40, "limit": 60}
```

```
edit {"path": "src/dates.py", "old_string": "    return datetime.strptime(text, \"%Y-%m-%d\")", "new_string": "    return datetime.strptime(text.strip(), \"%Y-%m-%d\")"}
```

```
multi_edit {"path": "src/app.py", "edits": [{"old_string": "import os\n", "new_string": "import os\nimport sys\n"}, {"old_string": "DEBUG = True", "new_string": "DEBUG = \"--debug\" in sys.argv"}]}
```

```
verify {"commands": ["python -m pytest -q tests/test_dates.py"]}
```

```
verify {}
```

```
open_in_editor {"path": "src/dates.py", "line": 42}
```
