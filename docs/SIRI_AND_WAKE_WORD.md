# Siri and the wake word

Ideas for working with Siri on Apple devices, and why "Hey Siri" and "Alexa" are so much better at hearing their wake word than Flash, with what Flash does and could do about it.

## Why Siri and Alexa hear their wake word so well

Flash listens for "Hey Flash" with Vosk, a general speech recognizer, held to a short list of phrases. It then hears the last few seconds again, freely, to check the name is really in them. Siri and Alexa work differently in five ways.

1. **A detector made for the phrase.** Apple and Amazon each train a small neural network on hundreds of thousands of recordings of exactly "Hey Siri" or "Alexa", said by many voices, in many rooms, at many distances. It scores every 10 ms of sound continuously. A general recognizer has never been taught the phrase as one thing. It has to build it out of words and wait for a pause before it decides.
2. **Two stages.** A tiny, always-on first stage (on the iPhone it runs on the motion coprocessor) passes anything likely to a much larger second model. Alexa sends the moment to the cloud for a third check before it answers. Each stage can be tuned to be generous, because the next one catches its mistakes.
3. **Your voice.** Setting up "Hey Siri" asks you to say it five times. Apple keeps a profile of your voice and checks every wake against it, which is why it rarely wakes for someone on TV.
4. **Hardware that cleans the sound first.** Several microphones steer toward the speaker (beamforming). The device takes its own speaker's sound out of what it hears (echo cancellation), and loudness is evened out before the detector ever runs.
5. **Tuning on enormous amounts of not-the-wake-word.** Thresholds are set against thousands of hours of TV, music and conversation, so false wakes are counted and pushed down to a few a week.

## What Flash does now

- **The listening model, held to the phrase**, then a second free hearing to confirm (the two-stage idea from item 2).
- **Your voice, new.** Settings, Voice, "Your voice", Teach it: say the wake word four times. Flash keeps how you say it (MFCC templates in `~/.flash/wake_voice`) and compares what it hears against them with dynamic time warping. A very close match wakes Flash even when the listening model missed the phrase. A fairly close one stands in for the free hearing when a noisy room drowned it out. Nothing is downloaded and nothing leaves the computer. See `flash/wake_voice.py`.
- **Browser clean-up.** The page asks the browser for echo cancellation and noise suppression on the microphone (item 4, in software).

## What could come next

- **A real wake-word detector.** openWakeWord's approach fits Flash: a fixed, shared speech-embedding model (about 1 MB, ONNX) and a tiny classifier on top, trained per wake word. The classifier can be trained on this computer from **synthetic speech**: Flash's own voices (Kokoro, Phonon, Siri's) say the wake word thousands of ways, mixed with room noise, music and other speech. This is how openWakeWord makes custom words without anyone recording them. It would replace the Vosk grammar as the first stage, scoring every 80 ms, with the free hearing and your voice as the later stages.
- **Learning from mistakes.** When you cancel straight after a wake, keep those seconds as a "not the wake word" example. When you say it twice in a row, keep the first as a missed one. Retrain the classifier or retune the thresholds weekly.
- **A level-evener before the detector** (automatic gain control), and a simple voice-activity gate so nothing is scored in silence. That saves power and avoids false wakes on hiss.
- **Not listening to itself.** While Flash is speaking, subtract its own output from the microphone, or at least raise the threshold.

## Siri

- **"Hey Siri, ask Flash".** The most dependable wake word is Siri's own. A Shortcut named "Ask Flash" that dictates the question, sends it to Flash's local API, and speaks the answer would let "Hey Siri, ask Flash what's on my calendar" work on the Mac. With Flash reachable on the home network, it would work from an iPhone, an iPad, HomePod or an Apple Watch too. Flash can write the Shortcut file, sign it with macOS's own `shortcuts sign`, and open it to be imported in one click.
- **More Shortcuts from the same idea.** "Start a Flash voice chat", "Flash, add a task", "Flash, read my briefing". Each is a Shortcut calling one Flash endpoint, so each gets its own Siri phrase.
- **Siri's voices.** Already in: Siri's own voices speak Flash's replies on a Mac (`flash/siri.py`), through a small helper because `say` and AppleScript cannot reach them.
- **Siri's hearing.** Already in: the "apple" listening model uses the speech recognition behind dictation and Siri, on the Mac itself where it can, with punctuation (`flash/apple_listen.py`).
- **Answers in Siri's style.** Short, spoken-first replies when a question arrives through Siri, with the long version in the Flash chat to read later.
- **Handing off.** "Hey Siri, continue on my Mac": a Shortcut that opens the same Flash chat on the Mac, where the answer has more room.
- **The other way round.** macOS's `shortcuts` command lists and runs Shortcuts. Offering them to Flash as tools would let it use anything a Shortcut can do: HomeKit scenes, Focus modes, Reminders, Health logging.
