// Flash in a window of its own on a Mac: the web UI Flash serves, in a native window, not a browser tab.
//
// launcher.py compiles this into ~/Applications/Flash.app (swiftc, from Apple's Command Line Tools) and puts
// launch.json beside it in Resources: the command that starts Flash's web UI out of sight, and where it logs. The
// app starts it (or finds the one already running), waits for ~/.flash/web/running.json to name a link that
// answers, and opens it. A flash:// link opens the window with the link's prompt waiting in the message box.
// Closing the window hides it; the Dock icon brings it back. The page gets the microphone (voice mode), file
// pickers (uploads) and downloads (to Downloads); links to other sites open in the browser.

import Cocoa
import WebKit

struct Launch: Decodable { let command: [String]; let log: String; let status: String; let running: String }

final class App: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var base: URL?
    var pendingPrompt: String?
    let launch: Launch = {
        let url = Bundle.main.url(forResource: "launch", withExtension: "json")!
        return try! JSONDecoder().decode(Launch.self, from: Data(contentsOf: url))
    }()

    func applicationDidFinishLaunching(_ note: Notification) {
        menus()
        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        config.websiteDataStore = .default()   // the page's own storage (its token, its preferences) kept between runs
        // The page makes room for the window's buttons, which sit over its top left corner.
        config.userContentController.addUserScript(WKUserScript(
            source: "document.documentElement.classList.add('mac-app')",
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsMagnification = true
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "Flash"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden        // the page says "Flash" itself; no title over it
        // An empty unified toolbar makes the title bar 52 points tall, which puts the close, minimise and zoom
        // buttons at its middle: on one line with the page's "Flash" mark, whose row is as tall.
        let bar = NSToolbar(identifier: "FlashBar")
        bar.showsBaselineSeparator = false
        window.toolbar = bar
        window.toolbarStyle = .unified
        window.isReleasedWhenClosed = false
        window.contentView = web
        window.center()
        window.setFrameAutosaveName("FlashWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        start()
    }

    // ----- Flash's web UI: the one running, or one started now -----
    func runningURL() -> URL? {
        guard let data = FileManager.default.contents(atPath: launch.running),
              let found = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let text = found["url"] as? String else { return nil }
        return URL(string: text)
    }

    func answers(_ url: URL, _ done: @escaping (Bool) -> Void) {
        var ask = URLRequest(url: url); ask.timeoutInterval = 1
        URLSession.shared.dataTask(with: ask) { _, reply, _ in
            done((reply as? HTTPURLResponse) != nil)
        }.resume()
    }

    func start() {
        if let url = runningURL() {
            answers(url) { ok in DispatchQueue.main.async { ok ? self.open(url) : self.startFlash() } }
        } else { startFlash() }
    }

    func startFlash() {
        let fm = FileManager.default
        try? fm.removeItem(atPath: launch.status)
        try? fm.removeItem(atPath: launch.running)
        try? fm.createDirectory(atPath: (launch.log as NSString).deletingLastPathComponent, withIntermediateDirectories: true)
        let quoted = launch.command.map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }.joined(separator: " ")
        let job = "\(quoted) >'\(launch.log)' 2>&1; echo $? >'\(launch.status)'"
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", job]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:\(NSHomeDirectory())/.local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        env["BROWSER"] = "/usr/bin/true"     // the window is the browser: Flash opens none of its own
        task.environment = env
        do { try task.run() } catch { fail("Flash could not be started: \(error.localizedDescription)"); return }
        wait(tries: 0)
    }

    func wait(tries: Int) {
        if let code = try? String(contentsOfFile: launch.status, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
           !code.isEmpty {
            let tail = (try? String(contentsOfFile: launch.log, encoding: .utf8))?.split(separator: "\n").suffix(12).joined(separator: "\n") ?? ""
            fail("Flash stopped as it started.\n\n\(tail)\n\n(All of it is in \(launch.log).)")
            return
        }
        if let url = runningURL() {
            answers(url) { ok in DispatchQueue.main.async { ok ? self.open(url) : self.again(tries) } }
        } else { again(tries) }
    }

    func again(_ tries: Int) {
        if tries > 160 { fail("Flash did not start in 40 seconds. What it said is in \(launch.log)."); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.wait(tries: tries + 1) }
    }

    func open(_ url: URL) {
        base = url
        var target = url.absoluteString
        if let prompt = pendingPrompt, let enc = prompt.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
            target += "#prompt=" + enc; pendingPrompt = nil
        }
        web.load(URLRequest(url: URL(string: target)!))
    }

    func fail(_ text: String) {
        let alert = NSAlert(); alert.messageText = "Flash can't start"; alert.informativeText = text
        alert.alertStyle = .critical; alert.runModal()
    }

    // ----- flash:// links -----
    func application(_ app: NSApplication, open urls: [URL]) {
        for url in urls where url.scheme == "flash" {
            let prompt = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "prompt" }?.value
            if let prompt = prompt, let base = base { pendingPrompt = prompt; open(base) }
            else if let prompt = prompt { pendingPrompt = prompt }
            window?.makeKeyAndOrderFront(nil)
        }
    }

    // ----- the window: hidden when closed, back from the Dock -----
    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { window.makeKeyAndOrderFront(nil) }
        return true
    }

    // ----- what a browser does for a page -----
    // other sites in the browser; Flash's own pages here
    func webView(_ view: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let base = base, url.host != base.host || url.port != base.port,
           ["http", "https", "mailto"].contains(url.scheme ?? "") {
            NSWorkspace.shared.open(url); decisionHandler(.cancel); return
        }
        if action.shouldPerformDownload { decisionHandler(.download); return }
        decisionHandler(.allow)
    }

    func webView(_ view: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    // a link that opens a new window (target=_blank)
    func webView(_ view: WKWebView, createWebViewWith config: WKWebViewConfiguration, for action: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // the microphone and camera, for voice mode and calls: macOS asks the user once, for the app
    @available(macOS 12.0, *)
    func webView(_ view: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin, initiatedByFrame frame: WKFrameInfo,
                 type: WKMediaCaptureType, decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(origin.host == base?.host ? .grant : .prompt)
    }

    // <input type=file>
    func webView(_ view: WKWebView, runOpenPanelWith params: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = params.allowsMultipleSelection
        panel.canChooseDirectories = params.allowsDirectories
        panel.beginSheetModal(for: window) { done in completionHandler(done == .OK ? panel.urls : nil) }
    }

    // alert(), confirm(): the page uses its own dialogs, but anything that slips through still shows
    func webView(_ view: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ view: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert(); alert.messageText = message; alert.addButton(withTitle: "OK"); alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    // downloads go to Downloads, under the name the page gives
    func webView(_ view: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ view: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let folder = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        var dest = folder.appendingPathComponent(suggestedFilename), n = 2
        let stem = (suggestedFilename as NSString).deletingPathExtension, ext = (suggestedFilename as NSString).pathExtension
        while FileManager.default.fileExists(atPath: dest.path) {
            dest = folder.appendingPathComponent(ext.isEmpty ? "\(stem) \(n)" : "\(stem) \(n).\(ext)"); n += 1
        }
        completionHandler(dest)
    }

    // the page gone blank (its process ended): load it again
    func webViewWebContentProcessDidTerminate(_ view: WKWebView) { if let base = base { open(base) } }

    // ----- the menu bar: Flash, Edit (so copy and paste work), View, Window -----
    func menus() {
        let bar = NSMenu()
        func menu(_ title: String, _ items: [NSMenuItem]) {
            let top = NSMenuItem(); let m = NSMenu(title: title); items.forEach { m.addItem($0) }; top.submenu = m; bar.addItem(top)
        }
        func item(_ t: String, _ a: Selector?, _ k: String, _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k); i.keyEquivalentModifierMask = mods; return i
        }
        menu("Flash", [item("About Flash", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""), .separator(),
                       item("Hide Flash", #selector(NSApplication.hide(_:)), "h"),
                       item("Quit Flash", #selector(NSApplication.terminate(_:)), "q")])
        menu("Edit", [item("Undo", Selector(("undo:")), "z"), item("Redo", Selector(("redo:")), "Z"), .separator(),
                      item("Cut", #selector(NSText.cut(_:)), "x"), item("Copy", #selector(NSText.copy(_:)), "c"),
                      item("Paste", #selector(NSText.paste(_:)), "v"), item("Select All", #selector(NSText.selectAll(_:)), "a")])
        menu("View", [item("Reload", #selector(reload), "r"), item("Actual Size", #selector(zoomReset), "0"),
                      item("Zoom In", #selector(zoomIn), "+"), item("Zoom Out", #selector(zoomOut), "-"), .separator(),
                      item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control])])
        menu("Window", [item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
                        item("Close", #selector(NSWindow.performClose(_:)), "w")])
        NSApp.mainMenu = bar
    }
    @objc func reload() { web.reload() }
    @objc func zoomReset() { web.pageZoom = 1 }
    @objc func zoomIn() { web.pageZoom = min(3, web.pageZoom + 0.1) }
    @objc func zoomOut() { web.pageZoom = max(0.5, web.pageZoom - 0.1) }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
