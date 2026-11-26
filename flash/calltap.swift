// flash-call-tap: what this Mac is playing (the other side of a call),
// as raw 16-bit mono PCM on stdout at the rate asked for, leaving out
// the processes named (Flash itself, so its own voice is not heard back).
// Built and run by flash/calltap.py. Needs macOS 14.2 or later, and the
// "System Audio Recording" permission the first time.
//
//   flash-call-tap RATE [PID ...]
//
// It stops when its stdin closes, which is when Flash lets it go.

import AudioToolbox
import CoreAudio
import Foundation

func fail(_ said: String) -> Never {
    FileHandle.standardError.write((said + "\n").data(using: .utf8)!)
    exit(1)
}

signal(SIGPIPE, SIG_IGN)
let args = CommandLine.arguments
let outRate = args.count > 1 ? (Double(args[1]) ?? 16000) : 16000
let leftOut = args.dropFirst(2).compactMap { pid_t($0) }

func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

// A process's audio object, from its pid: what a tap leaves out by.
func processObject(_ pid: pid_t) -> AudioObjectID? {
    var where_ = address(kAudioHardwarePropertyTranslatePIDToProcessObject)
    var pid = pid
    var object = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &where_,
                                            UInt32(MemoryLayout<pid_t>.size), &pid, &size, &object)
    return status == noErr && object != kAudioObjectUnknown ? object : nil
}

// The speakers in use now: the tap rides along with them.
func defaultOutputUID() -> String? {
    var where_ = address(kAudioHardwarePropertyDefaultOutputDevice)
    var device = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &where_, 0, nil, &size, &device) == noErr
    else { return nil }
    where_ = address(kAudioDevicePropertyDeviceUID)
    var uid: Unmanaged<CFString>?
    size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(device, &where_, 0, nil, &size, &uid) == noErr, let found = uid
    else { return nil }
    return found.takeRetainedValue() as String
}

let tap = CATapDescription(stereoGlobalTapButExcludeProcesses: leftOut.compactMap(processObject))
tap.uuid = UUID()
tap.isPrivate = true
tap.muteBehavior = .unmuted
var tapID = AudioObjectID(kAudioObjectUnknown)
guard AudioHardwareCreateProcessTap(tap, &tapID) == noErr else {
    fail("macOS would not let Flash hear the call: allow it under System Settings, Privacy & Security, System Audio Recording")
}

guard let outputUID = defaultOutputUID() else {
    AudioHardwareDestroyProcessTap(tapID)
    fail("no speakers to hear the call on")
}
let description: [String: Any] = [
    kAudioAggregateDeviceNameKey: "Flash call",
    kAudioAggregateDeviceUIDKey: UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey: outputUID,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
    kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                       kAudioSubTapUIDKey: tap.uuid.uuidString]],
]
var aggregate = AudioObjectID(kAudioObjectUnknown)
guard AudioHardwareCreateAggregateDevice(description as CFDictionary, &aggregate) == noErr else {
    AudioHardwareDestroyProcessTap(tapID)
    fail("could not listen to the Mac's sound")
}

// The tap's own format: float samples, usually 48 kHz stereo.
var formatAt = address(kAudioTapPropertyFormat)
var format = AudioStreamBasicDescription()
var formatSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
AudioObjectGetPropertyData(tapID, &formatAt, 0, nil, &formatSize, &format)
let inRate = format.mSampleRate > 0 ? format.mSampleRate : 48000
let channels = max(1, Int(format.mChannelsPerFrame))
let step = inRate / outRate
var phase = 0.0

var procID: AudioDeviceIOProcID?
let made = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregate, nil) { _, input, _, _, _ in
    let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
    guard let first = buffers.first, first.mData != nil else { return }
    let interleaved = buffers.count == 1
    let frames = Int(first.mDataByteSize) / MemoryLayout<Float>.size / (interleaved ? channels : 1)
    var pcm = [Int16]()
    pcm.reserveCapacity(Int(Double(frames) / step) + 2)
    // Mixed down to one channel and taken down to the rate asked for.
    while phase < Double(frames) {
        let i = Int(phase)
        var sum: Float = 0
        if interleaved {
            let samples = first.mData!.assumingMemoryBound(to: Float.self)
            for c in 0..<channels { sum += samples[i * channels + c] }
            sum /= Float(channels)
        } else {
            var n: Float = 0
            for buffer in buffers {
                if let data = buffer.mData { sum += data.assumingMemoryBound(to: Float.self)[i]; n += 1 }
            }
            sum /= max(1, n)
        }
        pcm.append(Int16(max(-1, min(1, sum)) * 32767))
        phase += step
    }
    phase -= Double(frames)
    pcm.withUnsafeBytes { bytes in
        _ = fwrite(bytes.baseAddress, 1, bytes.count, stdout)
        fflush(stdout)
    }
}
guard made == noErr, let proc = procID else {
    AudioHardwareDestroyAggregateDevice(aggregate)
    AudioHardwareDestroyProcessTap(tapID)
    fail("could not listen to the Mac's sound")
}
AudioDeviceStart(aggregate, proc)

// Until Flash lets go.
_ = FileHandle.standardInput.readDataToEndOfFile()
AudioDeviceStop(aggregate, proc)
AudioDeviceDestroyIOProcID(aggregate, proc)
AudioHardwareDestroyAggregateDevice(aggregate)
AudioHardwareDestroyProcessTap(tapID)
