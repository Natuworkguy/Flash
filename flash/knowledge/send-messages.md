---
name: send-messages
description: Read or send iMessages and texts on the user's Mac
---
- read_imessages reads recent messages, newest first, with names from Contacts and group chats named.
- send_imessage sends one in the user's name; they see it and approve it first.
- mark_imessages_read marks a conversation read.

These need computer actions on, on a Mac, with Messages and Full Disk Access allowed. Write messages the way the user writes: short and casual unless the context says otherwise. Never send to someone the user did not name.

## Examples

```
read_imessages {}
```

```
send_imessage {"to": "Mum", "text": "Landed, see you at 6!"}
```
