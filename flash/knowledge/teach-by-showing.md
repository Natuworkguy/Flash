---
name: teach-by-showing
description: Learn a task from a screen recording of the user doing it, and do it for them after
---
- "Watch how I do this", "learn this from my recording": the user shows the task once and you save it as a skill. In the browser they record with + then Record and narrate; the recording comes with its stills and their words. Anywhere else, give learn_from_recording the path of any screen recording (mp4, mov, webm, mkv).
- From the stills, work out the procedure, not a diary of the video: the goal, then numbered steps, each with the app or page, what to click or type, and what the screen shows once it is done. Keep values that change each time (a name, a date, an amount) as blanks to ask for or fill in. Ask about anything the stills do not show before you save it.
- Save it with skill_manage: a name for the kind of task, a description saying when to use it, and a line at the top that it was learned from a recording and runs with computer_use.
- To do it later: skill_view it, then do each step. Use the cheapest tool that reaches it (shell, the browser tools, an app's connector), and computer_use for the rest. Before each step, check the screen matches what the last step should have left; if it does not, stop and say what you see.
- Always ask the user first before a step that sends, posts, pays, buys, deletes or signs in, even when the recording did it without a pause. Never type passwords or payment details.
- When a run goes differently from the recording (a button moved, a new dialog), fix the skill with patch so next time is right.

## Examples

```
learn_from_recording {"path": "~/Movies/Screen Recording 2026-10-30 at 09.12.mov"}
```

```
skill_manage {"action": "create", "name": "file-an-expense", "description": "File an expense report in the company portal from a receipt", "content": "Learned from a recording. Runs with computer_use.\n\n1. Open the portal in the browser; the dashboard shows Expenses on the left.\n2. Click Expenses, then New report; an empty form opens.\n3. Fill in the date, the amount and the merchant from the receipt.\n4. Attach the receipt file.\n5. Ask the user, then click Submit; the report shows as Pending."}
```
