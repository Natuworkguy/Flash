---
name: make-phone-calls
description: Call someone, or take their next call, and talk on it for the user (UltraAuto, Mac)
---
- "Call John and tell him I'm running late", "ring the dentist and move my appointment to Friday", "take the next call from John and find out what he wants": use phone_call. It talks in your own voice through the iPhone's calling on the Mac, and only in UltraAuto.
- via facetime_audio calls with FaceTime Audio over the internet instead of the iPhone: when the user asks for FaceTime, or the person has only an email address.
- Put the whole errand in goal, in the user's words: what to say, what to find out, what they will and will not agree to. You cannot ask the user anything mid-call.
- You always say first that you are an automated assistant calling for the user. Never claim to be them.
- Agree to nothing they did not ask for: no payments, no personal details, no new plans. Say you will pass it on.
- After a call, tell the user how it went in a line or two, and anything they need to act on. The transcript is kept; give its path.
- answer waits in the background; the user is notified when the call has come and gone.
- "Talk to my friend", "say hi to everyone on this Meet": the user is already on a call (Google Meet, Zoom...). Use action join; who is optional. It only works once the user has set the call's microphone to BlackHole 2ch and its speaker to BlackHole 16ch: if they may not have, tell them that first. Flash never hangs up a joined call; it stops when told to, or when the call goes quiet.
- If the Mac lacks the loopback devices, say the one command that installs them and stop.

## Examples

```
phone_call {"action": "call", "who": "John Doe", "goal": "Tell him Sam is running about 15 minutes late to dinner, and ask if he wants Sam to bring anything."}
```

```
phone_call {"action": "answer", "who": "John Doe", "goal": "Find out what he is calling about and when he is free to talk to Sam.", "minutes": 60}
```

```
phone_call {"action": "join", "who": "Alex", "goal": "Say hello to Alex for Sam and ask how the move went."}
```
