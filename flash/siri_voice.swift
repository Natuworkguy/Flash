// Flash's Siri voice helper.
//
// Flash runs it with `swift siri_voice.swift`, kept running, and talks to it
// over stdin and stdout, one JSON line per request:
//
//   {"cmd": "list"}
//   {"cmd": "say", "voice": "com.apple.siri.natural.Nora", "text": "Hi",
//    "rate": 0.5}
//
// Each answer is one JSON line, then as many bytes of audio as its "bytes"
// says: 16-bit mono PCM at its "rate".
//
// It has to be run by `swift`, not compiled: macOS gives the Siri voices
// only to Apple's own signed programs, and a compiled copy of this file is
// shown the classic voices alone.

import AVFoundation
import Foundation

let synth = AVSpeechSynthesizer()

func answer(_ fields: [String: Any], _ audio: Data = Data()) {
    var meta = fields
    meta["bytes"] = audio.count
    guard var out = try? JSONSerialization.data(withJSONObject: meta) else { return }
    out.append(10)
    out.append(audio)
    FileHandle.standardOutput.write(out)
}

func siriVoices() -> [AVSpeechSynthesisVoice] {
    return AVSpeechSynthesisVoice.speechVoices().filter { $0.identifier.hasPrefix("com.apple.siri.") }
}

func list() {
    let voices = siriVoices().map { v -> [String: Any] in
        return ["id": v.identifier, "name": v.name, "language": v.language]
    }
    answer(["ok": true, "voices": voices])
}

// The words as 16-bit mono audio. The synthesizer hands over buffers, and
// an empty one when it has finished.
func say(_ voiceId: String, _ text: String, _ rate: Float) {
    guard let voice = siriVoices().first(where: { $0.identifier == voiceId }) else {
        answer(["ok": false, "error": "That Siri voice is not installed."])
        return
    }
    let utterance = AVSpeechUtterance(string: text)
    utterance.voice = voice
    utterance.rate = min(max(rate, AVSpeechUtteranceMinimumSpeechRate), AVSpeechUtteranceMaximumSpeechRate)

    var pcm = Data()
    var sampleRate = 0.0
    var finished = false
    var last = Date()

    synth.write(utterance) { buffer in
        guard let b = buffer as? AVAudioPCMBuffer else { return }
        last = Date()
        let frames = Int(b.frameLength)
        if frames == 0 {
            finished = true
            return
        }
        sampleRate = b.format.sampleRate
        var samples = [Int16](repeating: 0, count: frames)
        if let floats = b.floatChannelData {
            for i in 0..<frames {
                samples[i] = Int16(max(-1.0, min(1.0, floats[0][i])) * 32767)
            }
        } else if let ints = b.int16ChannelData {
            for i in 0..<frames { samples[i] = ints[0][i] }
        }
        samples.withUnsafeBufferPointer { pcm.append(Data(buffer: $0)) }
    }

    // Give up when nothing has come for a while, rather than hang Flash.
    let started = Date()
    while !finished && Date().timeIntervalSince(last) < 15 && Date().timeIntervalSince(started) < 120 {
        RunLoop.current.run(until: Date().addingTimeInterval(0.02))
    }
    if pcm.isEmpty {
        answer(["ok": false, "error": "That voice made no sound."])
        return
    }
    answer(["ok": true, "rate": Int(sampleRate)], pcm)
}

while let line = readLine() {
    guard let data = line.data(using: .utf8),
          let request = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
        answer(["ok": false, "error": "Not a request."])
        continue
    }
    switch request["cmd"] as? String ?? "" {
    case "list":
        list()
    case "say":
        say(request["voice"] as? String ?? "",
            request["text"] as? String ?? "",
            Float(request["rate"] as? Double ?? 0.5))
    default:
        answer(["ok": false, "error": "Unknown request."])
    }
}
