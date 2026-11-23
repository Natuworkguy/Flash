// Hears a recording with this Mac's own speech recognition (the engine behind dictation and Siri), on the Mac
// itself where it can. Flash builds this into a small app, so macOS can ask once whether it may listen, and opens
// it for each thing said:
//
//     AppleListen IN.wav OUT.json [LANGUAGE]
//
// OUT.json says {"ok": true, "text": "..."} or {"ok": false, "error": "..."}.
import Foundation
import Speech

let args = CommandLine.arguments
guard args.count >= 3 else { exit(2) }
let input = URL(fileURLWithPath: args[1])
let output = URL(fileURLWithPath: args[2])
let language = args.count > 3 ? args[3] : "en-US"

func finish(_ answer: [String: Any]) -> Never {
    if let data = try? JSONSerialization.data(withJSONObject: answer) {
        try? data.write(to: output, options: .atomic)
    }
    exit(0)
}

// asked once: macOS shows its own prompt the first time, and remembers
var status = SFSpeechRecognizer.authorizationStatus()
if status == .notDetermined {
    let asked = DispatchSemaphore(value: 0)
    SFSpeechRecognizer.requestAuthorization { answer in
        status = answer
        asked.signal()
    }
    asked.wait()
}
guard status == .authorized else {
    finish(["ok": false, "error": "Speech recognition is off for Flash. Turn it on in System Settings, Privacy & Security, Speech Recognition."])
}
guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: language)), recognizer.isAvailable else {
    finish(["ok": false, "error": "This Mac cannot hear \(language) right now."])
}
let request = SFSpeechURLRecognitionRequest(url: input)
request.shouldReportPartialResults = false
// on the Mac itself when it can, so nothing said leaves it
if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
if #available(macOS 13, *) { request.addsPunctuation = true }
let task = recognizer.recognitionTask(with: request) { result, error in
    if let result = result, result.isFinal {
        finish(["ok": true, "text": result.bestTranscription.formattedString,
                "on_device": request.requiresOnDeviceRecognition])
    }
    if let error = error {
        // nothing said is not a failure
        let code = (error as NSError).code
        if code == 1110 || code == 203 { finish(["ok": true, "text": ""]) }
        finish(["ok": false, "error": error.localizedDescription])
    }
}
_ = task
RunLoop.main.run(until: Date(timeIntervalSinceNow: 90))
finish(["ok": false, "error": "Speech recognition took too long."])
