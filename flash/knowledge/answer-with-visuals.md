---
name: answer-with-visuals
description: Pick the right rich block for an answer: chart, comparison, stats, steps, calculator, choices, form
---
Replies can carry ```ui blocks (their syntax is in your system prompt). Use one when it makes the answer clearer than text:
- chart: numbers over time or across categories.
- compare: two or three options side by side.
- stats: a few headline numbers.
- steps or flow: a procedure or a process.
- calc: a formula the user will want to try with their own numbers.
- choices: offering the next action as buttons.
- form: collecting several details at once.
- checklist: something to tick off.

One or two per reply at most, each with a sentence of text around it saying what it shows. Use real numbers from tools or the user, never made-up data presented as fact. For larger dashboards or interactive tools, build a web page instead (see build-a-web-page).

## Examples

A chart in a reply is a ui block, not a tool call; see the format in your system prompt. For something richer, build a page:
```
send_html {"path": "dashboard.html", "caption": "Sales by month"}
```
