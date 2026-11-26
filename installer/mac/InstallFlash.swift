// Install Flash.app: Flash's installer in a window of its own on a Mac. No terminal, no browser.
//
// The app holds install.sh and the setup page (installer/window/index.html and its film) in Resources. It runs
// install.sh with SETUP_WINDOW_DIR set to a folder of its own; the script writes status.json there as it goes and
// the app hands each new one to the page, in a native window (WebKit, inside the app). When a step needs an
// administrator's password, macOS asks for it in its own dialog (sudo with an askpass helper). At the end the
// page's Open button starts Flash (open.json, written by the script) and Close quits.
//
// Built by installer/mac/build.sh:  swiftc -O -o "Install Flash.app/Contents/MacOS/Install Flash" InstallFlash.swift

import Cocoa
import WebKit

final class Installer: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var task: Process?
    var timer: Timer?
    var lastStatus = ""
    let work: URL = FileManager.default.temporaryDirectory
        .appendingPathComponent("flash-setup-\(ProcessInfo.processInfo.processIdentifier)")
    var resources: URL { Bundle.main.resourceURL! }

    func applicationDidFinishLaunching(_ note: Notification) {
        menus()
        try? FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "setup")
        config.mediaTypesRequiringUserActionForPlayback = []
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 980, height: 640),
                          styleMask: [.titled, .closable, .miniaturizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "Install Flash"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.isMovableByWindowBackground = true
        window.backgroundColor = .black
        window.contentView = web
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        let page = resources.appendingPathComponent("window/index.html")
        web.loadFileURL(page, allowingReadAccessTo: resources.appendingPathComponent("window"))
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if task == nil { run() }
    }

    // ----- the script -----
    func run() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [resources.appendingPathComponent("install.sh").path] + extraArguments()
        var env = ProcessInfo.processInfo.environment
        // Where Homebrew and pipx put things, which an app started from Finder does not have on its PATH.
        let home = NSHomeDirectory()
        let shims = work.appendingPathComponent("bin")
        makeSudoShim(in: shims)
        env["PATH"] = ([shims.path, "/opt/homebrew/bin", "/usr/local/bin", "\(home)/.local/bin"]
                       + (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin").split(separator: ":").map(String.init))
            .joined(separator: ":")
        env["SETUP_WINDOW_DIR"] = work.path
        env["SUDO_ASKPASS"] = resources.appendingPathComponent("askpass.sh").path
        env["TERM"] = "dumb"
        p.environment = env
        p.currentDirectoryURL = URL(fileURLWithPath: home)
        let log = work.appendingPathComponent("install.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: log) {
            p.standardOutput = handle
            p.standardError = handle
        }
        p.standardInput = FileHandle.nullDevice
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.finished(proc.terminationStatus) }
        }
        do {
            try p.run()
            task = p
        } catch {
            fail("Could not start the installer: \(error.localizedDescription)")
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in self?.pump() }
    }

    func extraArguments() -> [String] {
        // "Install Flash (dev).app", or a dev marker file in Resources, installs the dev branch.
        let name = Bundle.main.bundleURL.lastPathComponent.lowercased()
        let marker = resources.appendingPathComponent("dev")
        return name.contains("(dev)") || FileManager.default.fileExists(atPath: marker.path) ? ["--dev"] : []
    }

    /// `sudo` that always asks through the dialog: Ollama's own installer calls plain sudo.
    func makeSudoShim(in folder: URL) {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let shim = folder.appendingPathComponent("sudo")
        let text = "#!/bin/bash\nexec /usr/bin/sudo -A \"$@\"\n"
        try? text.write(to: shim, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shim.path)
    }

    func pump() {
        let status = work.appendingPathComponent("status.json")
        guard let data = try? Data(contentsOf: status), let text = String(data: data, encoding: .utf8),
              text != lastStatus, (try? JSONSerialization.jsonObject(with: data)) != nil else { return }
        lastStatus = text
        web.evaluateJavaScript("window.setupStatus(\(text))", completionHandler: nil)
    }

    func finished(_ code: Int32) {
        pump()
        timer?.invalidate()
        if code != 0 && !lastStatus.contains("\"failed\"") && !lastStatus.contains("\"done\": true") {
            let tail = (try? String(contentsOf: work.appendingPathComponent("install.log"), encoding: .utf8))?
                .split(separator: "\n").suffix(1).joined() ?? ""
            fail("The installer stopped" + (tail.isEmpty ? "." : ": \(tail)"))
        }
    }

    func fail(_ why: String) {
        var status = (try? JSONSerialization.jsonObject(with: Data(lastStatus.utf8)) as? [String: Any]) ?? [
            "product": "Flash", "accent": "#ff8a5c", "steps": [], "done": false, "doneNote": ""]
        status["failed"] = why
        if let data = try? JSONSerialization.data(withJSONObject: status), let text = String(data: data, encoding: .utf8) {
            lastStatus = text
            web.evaluateJavaScript("window.setupStatus(\(text))", completionHandler: nil)
        }
    }

    // ----- the page's buttons -----
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        let what = message.body as? String ?? ""
        if what == "open" { openFlash() }
        if what == "open" || what == "close" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { NSApp.terminate(nil) }
        }
    }

    func openFlash() {
        let file = work.appendingPathComponent("open.json")
        guard let data = try? Data(contentsOf: file),
              let found = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let command = found["cmd"] as? [String], let first = command.first else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: first)
        p.arguments = Array(command.dropFirst())
        p.standardInput = FileHandle.nullDevice
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if let t = task, t.isRunning {
            let alert = NSAlert()
            alert.messageText = "Flash is still being installed."
            alert.informativeText = "Stopping now leaves it half set up. You can run the installer again later."
            alert.addButton(withTitle: "Keep installing")
            alert.addButton(withTitle: "Stop")
            if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
            t.terminate()
        }
        return .terminateNow
    }

    func menus() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Install Flash", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = Installer()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
