---
name: send-messages
description: Read or send iMessages and texts on the user's Mac, with files
---
- read_imessages reads recent messages, newest first, with names from Contacts and group chats named. A message with files says so: [attachment: name (kind) at its path on the Mac]; view_image or read can open that path.
- send_imessage sends one in the user's name; they see it and approve it first.
- send_imessage can send a file too: attachment, a full path on the Mac (home, a temp folder or /Volumes; hidden paths are refused). It goes after the text, and text may be left empty to send only the file.
- mark_imessages_read marks a conversation read.

These need computer actions on, on a Mac, with Messages and Full Disk Access allowed. Write messages the way the user writes: short and casual unless the context says otherwise. Never send to someone the user did not name.

## Examples

```
read_imessages {}
```

```
send_imessage {"to": "Mum", "text": "Landed, see you at 6!"}
```

```
send_imessage {"to": "Sam", "text": "Here's the ticket", "attachment": "/Users/me/Downloads/ticket.pdf"}
```
