---
name: check-the-fine-print
description: Read terms, contracts and sign-up pages for traps: auto-renewals, cancellation hoops, fees, data sharing
---
1. Get the real text: fetch the terms, the privacy policy and the pricing page (or open_page when it needs a browser), or read the file or photo they gave you. Read all of it; the trap is rarely in the first screen.
2. Look for each of these, and quote the line that says it, with where it is:
   - Auto-renewal: when it renews, at what price (the price after a trial or intro offer), and how much notice they give.
   - Cancelling: how (online, only by phone, by post), the notice period, fees for leaving early, and whether a trial needs cancelling before a date.
   - Money: fees on top of the headline price, price rises they may make at will, minimum terms, deposits, what is refundable.
   - Data: what they collect, who they sell or share it with, whether it trains models, how to opt out and delete it.
   - Rights given up: arbitration instead of court, a class action waiver, the right to change the terms without notice, automatic consent to marketing.
   - Design tricks on the page itself: pre-ticked boxes, a confirm-shaming "no thanks, I like paying more", a countdown that resets, a cancel link hidden in settings.
3. Answer with a card block: a verdict badge (fine, careful, avoid) with its tone, then rows for each trap found, the worst first, and what to do about each (set a reminder before the trial ends, opt out here, cancel this way).
4. Offer the follow-ups that help: a reminder the day before a trial converts, the cancellation or opt-out email drafted, the date to cancel by in the calendar.

Pitfalls:
- Quote, never paraphrase into something scarier or milder than the text. Missing is "the terms do not say".
- This is reading, not legal advice: for real stakes (a lease, an employment contract), say a lawyer should look, and which clauses to ask about.

## Examples

```
fetch {"url": "https://example.com/terms"}
```

```
remind_me {"action": "add", "text": "Cancel the streaming trial before it starts charging", "when": "2026-12-10 09:00"}
```
