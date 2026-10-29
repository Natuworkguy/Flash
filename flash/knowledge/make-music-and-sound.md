---
name: make-music-and-sound
description: Compose music as MIDI, make sound effects or tones, or analyse an audio file
---
Music: make_midi writes and plays a MIDI file.
- Times are in beats: 0, 1, 2, 3 are the beats of a 4/4 bar, 4 starts the next.
- One track per part: melody, chords, bass, drums. Keep a key and a chord progression (for example I-V-vi-IV) so the parts fit.
- Check the result's note counts, ranges and length against what you meant; revise with all tracks and the same path.

Sounds: make_sound writes a WAV from a short score: notes like C4, F#3, Eb5 or a number of Hz; C4+E4+G4 for a chord; :2 to hold two beats; rest for silence. Waves: sine (soft), triangle, square (retro), saw (bright), noise (percussion, whooshes). Good for alerts, jingles and test tones.

Listening: listen_to_audio measures a file: length, loudness, clipping, silence and where sound is, its pitch as a note, brightness and tempo. Use it to check a mix is not clipping, find where a recording starts, or tell what note something is. For speech, transcribe it instead.

## Examples

Four bars: chords, bass and drums.
```
make_midi {"path": "~/Downloads/loop.mid", "tempo": 96, "title": "Loop", "tracks": [{"name": "chords", "instrument": "electric piano", "notes": [[["C4", "E4", "G4"], 0, 4], [["G3", "B3", "D4"], 4, 4], [["A3", "C4", "E4"], 8, 4], [["F3", "A3", "C4"], 12, 4]]}, {"name": "bass", "instrument": "bass", "notes": [["C2", 0, 4], ["G2", 4, 4], ["A2", 8, 4], ["F2", 12, 4]]}, {"name": "drums", "instrument": "drums", "notes": [["kick", 0, 1], ["snare", 1, 1], ["kick", 2, 1], ["snare", 3, 1]]}]}
```

A notification chime.
```
make_sound {"notes": "E5:0.5 G5:0.5 C6:2", "out": "chime.wav", "wave": "triangle", "bpm": 160}
```

```
listen_to_audio {"path": "mix.wav"}
```
