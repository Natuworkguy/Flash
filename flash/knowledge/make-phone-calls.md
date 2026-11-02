---
name: make-phone-calls
description: Call someone, or take their next call, and talk on it for the user (UltraAuto, Mac)
---
## When the user asks without saying what

If the user says only "use this skill, ask me what it needs", ask in two steps, never one form with every field:

1. First ask only which of these, as a ```ui choices block, in this order:
   - Talk on the Google Meet call I'm on (this Mac)
   - Talk on a Zoom or Teams call I'm on (this Mac)
   - Call someone (iPhone or FaceTime)
   - Take someone's next call
2. Then ask only what that choice needs:
   - A call on this Mac (Meet, Zoom, Teams): action join. Do not ask who to call, or iPhone or FaceTime. Ask only, and as optional, what to say or do on the call; if they skip it, the goal is to greet everyone for the user and help as asked. Then go straight to the two join steps below.
   - Call someone: who, what the call is for, and iPhone or FaceTime.
   - Take someone's next call: whose call, and what to find out.

## Doing it

- "Call John and tell him I'm running late", "ring the dentist and move my appointment to Friday", "take the next call from John and find out what he wants": use phone_call. It talks in your own voice through the iPhone's calling on the Mac, and only in UltraAuto.
- via facetime_audio calls with FaceTime Audio over the internet instead of the iPhone: when the user asks for FaceTime, or the person has only an email address.
- Put the whole errand in goal, in the user's words: what to say, what to find out, what they will and will not agree to. You cannot ask the user anything mid-call.
- You always say first that you are an automated assistant calling for the user. Never claim to be them.
- Agree to nothing they did not ask for: no payments, no personal details, no new plans. Say you will pass it on.
- After a call, tell the user how it went in a line or two, and anything they need to act on. The transcript is kept; give its path.
- answer waits in the background; the user is notified when the call has come and gone.
- "Talk to my friend on Google Meet", "say hi to everyone on this Zoom", "talk on the Meet I'm on": the user is already on a call open on this Mac. Use action join; who is optional and via is not used; app is the call app (Meet unless they named Zoom or Teams). It goes in two steps:
  1. Call phone_call join without ready. It joins nothing: it gives you the exact words to say, with this Mac's device names in them, such as "Okay. Set the mic to BlackHole 2ch and speakers to BlackHole 16ch in Meet and tell me once it's set." Say only that, and end your turn.
  2. When the user says it is set ("set", "done", "ok it's set"), call phone_call join again, the same goal, with ready true. Only then do you talk on the call.
- Flash never hangs up a joined call; it stops when told to, or when the call goes quiet.
- While Flash is on a call, the web page shows the call as it goes, turn by turn, with a button for the user to add their own mic to it. Stop ends the call at once, and the Mac's own mic and speakers come back.
- If the Mac lacks the loopback devices, say the one command that installs them and stop.

## Examples

```
phone_call {"action": "call", "who": "John Doe", "goal": "Tell him Sam is running about 15 minutes late to dinner, and ask if he wants Sam to bring anything."}
```

```
phone_call {"action": "answer", "who": "John Doe", "goal": "Find out what he is calling about and when he is free to talk to Sam.", "minutes": 60}
```

```
phone_call {"action": "join", "app": "Meet", "goal": "Say hello to everyone for Sam and help with whatever they ask."}
```

```
phone_call {"action": "join", "who": "Alex", "app": "Meet", "goal": "Say hello to Alex for Sam and ask how the move went."}
```

Then, once the user says it's set:

```
phone_call {"action": "join", "who": "Alex", "app": "Meet", "ready": true, "goal": "Say hello to Alex for Sam and ask how the move went."}
```
