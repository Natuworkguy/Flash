---
name: weigh-a-decision
description: Help the user decide between options with real trade-offs
---
- For a quick choice, compare options directly: a compare block, the criteria that matter, and a recommendation with the reason.
- For a real decision (a job offer, an architecture, a purchase), debate has two sides argue it out, each answering the other, then a summary. Give the question, the context, and optionally named sides and rounds. The user watches and can steer or stop it. Then say what you make of the summary; do not repeat it. At most once a turn.
- ask_system_one gets a quick probability from a small judging model: yes_no, a choice among options, or a score. Good as a second opinion before a risky step.

Always end with a clear recommendation and what would change it.

## Examples

```
debate {"question": "Should I take the startup offer or stay at my current job?", "context": "Current: 95k, stable, bored. Offer: 80k plus 0.4% equity, Series A, fully remote. Two kids, mortgage, 6 months of savings.", "rounds": 2}
```

```
ask_system_one {"question": "Is this migration safe to run on the live database without downtime?", "kind": "yes_no", "context": "ALTER TABLE orders ADD COLUMN refunded boolean DEFAULT false; Postgres 16, 40M rows."}
```
