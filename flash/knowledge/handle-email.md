---
name: handle-email
description: Read, summarise, triage, draft or send email
---
- check_inbox lists the newest emails (unread_only, query, folder, account); each has a uid. Nothing is marked read.
- read_email reads one in full by uid.
- send_email sends from the user's address, or replies with reply_to; the user sees it and says yes or no every time.

Triage: list, read the ones that matter, then summarise: who needs a reply, what is urgent, what can wait, with each email's link.
Writing: write as the user would, in their voice and name, short and specific, with a clear ask. For a reply, quote nothing they already have; answer the questions asked.

Email is set up with /email connect or under Settings, Email. If the tools are missing, say how to connect it.

## Examples

```
check_inbox {"unread_only": true, "limit": 20}
```

```
read_email {"uid": "4821"}
```

```
send_email {"reply_to": "4821", "body": "Hi Sam,\n\nThursday at 2 works. I'll send the agenda tomorrow.\n\nNathan"}
```
