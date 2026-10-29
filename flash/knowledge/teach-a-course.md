---
name: teach-a-course
description: Teach a subject as an interactive course ("teach me X")
---
Use the tutor tool whenever someone wants to learn something: a language, a tool, a topic, an exam. On the web page the subject becomes a course card the user works through on their own, with a path of lessons, quizzes, code that runs, points and a streak.

1. Plan a first unit of 6 to 12 small steps, groundwork first. Each step teaches one checkable idea.
2. Call tutor add once with all of them. Give every step:
   - id (short, kebab-case) and title (two or three words, shown on the path)
   - text: the idea in one sentence
   - needs: the ids it builds on
   - lesson: two to four short Markdown paragraphs that teach it, with a small example (a code block for code)
   - something to answer, mixing the kinds through a unit so it stays lively:
     - quiz: question, 2 to 6 choices, answer (the right choice's place, from 0), explain (one sentence on why)
     - blank: a prompt with ___ where the answer goes, and accept, every answer that counts ("printf", "printf()"); case and spacing never matter
     - order: a prompt and 3 to 8 items given in the right order, which the user puts back in order (steps of a process, lines of a program, words of a statement)
     - exercise, for programming and the command line: task, language (c, cpp, python, javascript, rust, go, java, or shell for Linux), expect (exactly what a right answer prints), optional starter code, files the task starts with, and verify (a shell script whose output is checked instead, for tasks that change files)
   - Only a unit's first step, with no needs, may be just a lesson with nothing to answer. Any other is refused.
3. Reply in a sentence or two. Do not paste the lessons or ask their questions in chat: the card shows them and marks answers itself.
4. When they ask for help on a step ("Ask Flash" sends their code and its output), give a hint that points at the mistake, never the full answer.
5. When the card says the unit is complete, or they ask for more, call tutor graph to see what is there, then add the next unit: new ideas only (never re-teach a step that exists), each with needs on earlier ones and something to answer.

In the terminal there is no card: teach one step at a time, ask its question, and record the answer with measure (right true or false). For exercises pass their code word for word to check. Never call measure without an answer they gave.

Pitfalls:
- expect has to be exact: "Hello, World!" is not "Hello World". Use match contains or regex when the output may vary.
- Write starter code with real newlines, not the two characters backslash and n.
- A quiz's wrong choices should be plausible mistakes, not jokes.

## Examples

Type the answer, and put in order.
```
tutor {"action": "add", "subject": "C", "propositions": [{"id": "include", "title": "Headers", "needs": ["main"], "text": "#include brings in a library's declarations.", "lesson": "`#include <stdio.h>` lets you use `printf`.", "blank": {"prompt": "To use printf, write #include <___> at the top.", "accept": ["stdio.h"], "explain": "stdio.h declares the standard input and output functions."}}, {"id": "compile-steps", "title": "Building", "needs": ["include"], "text": "C is preprocessed, compiled, linked, then run.", "lesson": "Your source goes through several stages before it runs.", "order": {"prompt": "Put the stages in order", "items": ["Preprocess", "Compile", "Link", "Run"], "explain": "Headers are pulled in first; the linker joins compiled pieces."}}]}
```

Start a unit (two of its steps shown).
```
tutor {"action": "add", "subject": "C", "propositions": [{"id": "main", "title": "main()", "text": "A C program starts running in main.", "lesson": "Every C program starts at `main`:\n\n```c\nint main(void) {\n    return 0;\n}\n```", "quiz": {"question": "Where does a C program start running?", "choices": ["The first line of the file", "In main", "In printf"], "answer": 1, "explain": "The system calls main; everything else runs because main calls it."}}, {"id": "printf", "title": "Printing", "needs": ["main"], "text": "printf prints text; \\n ends a line.", "lesson": "`printf` from `<stdio.h>` prints text.", "exercise": {"task": "Print `Hello, World!`", "language": "c", "expect": "Hello, World!", "starter": "#include <stdio.h>\n\nint main(void) {\n    \n    return 0;\n}\n"}}]}
```

A Linux task checked by what it changed.
```
tutor {"action": "add", "subject": "Linux", "propositions": [{"id": "mkdir-mv", "title": "Folders", "text": "mkdir makes a folder; mv moves files.", "lesson": "`mkdir name` makes a folder. `mv file folder/` moves a file into it.", "exercise": {"task": "Put notes.txt in a new folder called archive.", "language": "shell", "files": {"notes.txt": "keep me"}, "expect": "notes.txt\ngone", "verify": "ls archive; test ! -e notes.txt && echo gone"}}]}
```

In the terminal, mark an answer they gave.
```
tutor {"action": "measure", "subject": "C", "id": "main", "right": true}
```

In the terminal, run their code.
```
tutor {"action": "check", "subject": "C", "id": "printf", "answer": "#include <stdio.h>\nint main(void){ printf(\"Hello, World!\\n\"); }"}
```
