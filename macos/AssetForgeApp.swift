// AssetForge.app: a native macOS window around the AssetForge web UI.
//
// The app starts `python -m assetforge ui --json --port 0 --parent-pid <pid>` (paths come from
// Contents/Resources/launch.json, written by `assetforge app install`), reads the one JSON line it
// prints ({"url": ...}), and shows that URL in a WKWebView. Quitting the app stops the server; the
// server also exits by itself if this process disappears.
//
// Build: swiftc -O -framework Cocoa -framework WebKit -o AssetForge AssetForgeApp.swift

import Cocoa
import WebKit

struct LaunchConfig: Decodable {
    let python: String
    let repo: String
}

struct ServerInfo: Decodable {
    let url: String
    let home: String
}

enum ServerError: Error {
    case message(String)
}

/// Starts and supervises the Python UI server.
final class ServerProcess {
    private var process: Process?
    private var stdoutBuffer = Data()
    private(set) var stderrLines: [String] = []
    private var finished = false

    func start(config: LaunchConfig, library: String?, completion: @escaping (Result<ServerInfo, ServerError>) -> Void) {
        stop()
        finished = false
        stdoutBuffer = Data()
        stderrLines = []

        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.python)
        var args = ["-m", "assetforge"]
        if let library, !library.isEmpty { args += ["--home", library] }
        args += ["ui", "--json", "--port", "0", "--parent-pid", String(ProcessInfo.processInfo.processIdentifier)]
        process.arguments = args
        process.currentDirectoryURL = URL(fileURLWithPath: config.repo)
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        // Apps launched from Finder get a minimal PATH; ffmpeg/ffprobe usually live in Homebrew.
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        process.environment = env

        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err

        let finish: (Result<ServerInfo, ServerError>) -> Void = { [weak self] result in
            DispatchQueue.main.async {
                guard let self, !self.finished else { return }
                self.finished = true
                completion(result)
            }
        }

        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self, !data.isEmpty else { return }
            self.stdoutBuffer.append(data)
            if let newline = self.stdoutBuffer.firstIndex(of: 0x0A) {
                let line = self.stdoutBuffer.prefix(upTo: newline)
                handle.readabilityHandler = nil
                if let info = try? JSONDecoder().decode(ServerInfo.self, from: line) {
                    finish(.success(info))
                } else {
                    finish(.failure(.message("The AssetForge server printed something unexpected.")))
                }
            }
        }
        err.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self, !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                self.stderrLines += text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
                if self.stderrLines.count > 200 { self.stderrLines.removeFirst(self.stderrLines.count - 200) }
            }
        }
        process.terminationHandler = { proc in
            finish(.failure(.message("The AssetForge server stopped (exit code \(proc.terminationStatus)).")))
        }

        do {
            try process.run()
            self.process = process
        } catch {
            finish(.failure(.message("Couldn't start \(config.python): \(error.localizedDescription)")))
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 45) {
            finish(.failure(.message("The AssetForge server didn't start within 45 seconds.")))
        }
    }

    func stop() {
        guard let process, process.isRunning else { self.process = nil; return }
        process.terminationHandler = nil
        process.terminate()
        let deadline = Date().addingTimeInterval(3)
        while process.isRunning && Date() < deadline { usleep(50_000) }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        self.process = nil
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let server = ServerProcess()
    private var config: LaunchConfig?
    private let libraryKey = "LibraryPath"

    private var library: String? {
        get { UserDefaults.standard.string(forKey: libraryKey) }
        set { UserDefaults.standard.set(newValue, forKey: libraryKey) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        let configuration = WKWebViewConfiguration()
        configuration.preferences.isElementFullscreenEnabled = true
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "AssetForge"
        window.minSize = NSSize(width: 900, height: 600)
        window.contentView = webView
        window.center()
        window.setFrameAutosaveName("AssetForgeMainWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        loadConfig()
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) { server.stop() }

    // MARK: server

    private func loadConfig() {
        guard let url = Bundle.main.url(forResource: "launch", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let parsed = try? JSONDecoder().decode(LaunchConfig.self, from: data) else {
            config = nil
            return
        }
        config = parsed
    }

    private func startServer() {
        guard let config else {
            showError("This app bundle has no launch configuration.", hint: "Rebuild it with: assetforge app install")
            return
        }
        showLoading()
        server.start(config: config, library: library) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let info):
                self.window.subtitle = info.home
                if let url = URL(string: info.url) { self.webView.load(URLRequest(url: url)) }
            case .failure(.message(let message)):
                self.showError(message, hint: "Library: \(self.library ?? "~/.assetforge")\nPython: \(config.python)")
            }
        }
    }

    // MARK: pages

    private func page(_ body: String) -> String {
        """
        <!doctype html><html><head><meta charset="utf-8"><style>
        :root { color-scheme: light dark; }
        body { font: 14px -apple-system, system-ui; margin: 0; height: 100vh; display: flex; align-items: center;
               justify-content: center; background: Canvas; color: CanvasText; }
        .box { max-width: 720px; padding: 32px; }
        h1 { font-size: 20px; margin: 0 0 8px; }
        pre { background: rgba(127,127,127,.12); padding: 12px; border-radius: 8px; max-height: 320px; overflow: auto;
              white-space: pre-wrap; font: 12px ui-monospace, Menlo, monospace; }
        a.btn { display: inline-block; margin-top: 12px; padding: 7px 14px; border-radius: 8px; background: #2a78d6;
                color: white; text-decoration: none; font-weight: 600; }
        .spin { width: 22px; height: 22px; border: 3px solid rgba(127,127,127,.3); border-top-color: #2a78d6;
                border-radius: 50%; animation: s .8s linear infinite; margin: 0 auto 14px; }
        @keyframes s { to { transform: rotate(360deg); } }
        </style></head><body><div class="box">\(body)</div></body></html>
        """
    }

    private func escape(_ text: String) -> String {
        text.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }

    private func showLoading() {
        webView.loadHTMLString(page("<div class='spin'></div><div style='text-align:center'>Starting AssetForge…</div>"),
                               baseURL: nil)
    }

    private func showError(_ message: String, hint: String) {
        let log = server.stderrLines.suffix(40).joined(separator: "\n")
        let html = page("""
            <h1>AssetForge couldn't start</h1><p>\(escape(message))</p><pre>\(escape(hint))</pre>
            \(log.isEmpty ? "" : "<p>Server output:</p><pre>\(escape(log))</pre>")
            <a class="btn" href="assetforge-action://retry">Retry</a>
            &nbsp;<a class="btn" href="assetforge-action://open-library">Open Library…</a>
            """)
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: navigation

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { return decisionHandler(.allow) }
        if url.scheme == "assetforge-action" {
            decisionHandler(.cancel)
            if url.host == "retry" { startServer() } else if url.host == "open-library" { openLibrary(nil) }
            return
        }
        let local = url.host == "127.0.0.1" || url.host == "localhost"
        if url.scheme == "about" || url.scheme == "data" || local || navigationAction.targetFrame == nil && local {
            return decisionHandler(.allow)
        }
        NSWorkspace.shared.open(url)  // anything else opens in the default browser
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // MARK: menu actions

    @objc func openLibrary(_ sender: Any?) {
        let panel = NSOpenPanel()
        panel.title = "Choose an AssetForge library folder"
        panel.message = "Pick an existing library (it contains catalog.sqlite3) or an empty folder to start a new one."
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Open Library"
        if let current = library { panel.directoryURL = URL(fileURLWithPath: current) }
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let url = panel.url, let self else { return }
            self.library = url.path
            self.startServer()
        }
    }

    @objc func useDefaultLibrary(_ sender: Any?) {
        library = nil
        startServer()
    }

    @objc func reload(_ sender: Any?) { webView.reload() }
    @objc func zoomIn(_ sender: Any?) { webView.pageZoom = min(3, webView.pageZoom + 0.1) }
    @objc func zoomOut(_ sender: Any?) { webView.pageZoom = max(0.5, webView.pageZoom - 0.1) }
    @objc func actualSize(_ sender: Any?) { webView.pageZoom = 1 }
    @objc func restartServer(_ sender: Any?) { startServer() }

    private func buildMenu() {
        let main = NSMenu()
        func item(_ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = [.command],
                  target: AnyObject? = nil) -> NSMenuItem {
            let menuItem = NSMenuItem(title: title, action: action, keyEquivalent: key)
            menuItem.keyEquivalentModifierMask = mods
            menuItem.target = target
            return menuItem
        }
        func submenu(_ title: String, _ items: [NSMenuItem]) {
            let holder = NSMenuItem()
            let menu = NSMenu(title: title)
            items.forEach(menu.addItem)
            holder.submenu = menu
            main.addItem(holder)
        }
        submenu("AssetForge", [
            item("About AssetForge", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            item("Hide AssetForge", #selector(NSApplication.hide(_:)), "h"),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            item("Quit AssetForge", #selector(NSApplication.terminate(_:)), "q"),
        ])
        submenu("File", [
            item("Open Library…", #selector(openLibrary(_:)), "o", target: self),
            item("Use Default Library (~/.assetforge)", #selector(useDefaultLibrary(_:)), target: self),
            .separator(),
            item("Restart Server", #selector(restartServer(_:)), "r", [.command, .shift], target: self),
            .separator(),
            item("Close Window", #selector(NSWindow.performClose(_:)), "w"),
        ])
        submenu("Edit", [
            item("Undo", Selector(("undo:")), "z"),
            item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"),
            item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"),
            item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        submenu("View", [
            item("Reload", #selector(reload(_:)), "r", target: self),
            .separator(),
            item("Actual Size", #selector(actualSize(_:)), "0", target: self),
            item("Zoom In", #selector(zoomIn(_:)), "+", target: self),
            item("Zoom Out", #selector(zoomOut(_:)), "-", target: self),
            .separator(),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        submenu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:))),
        ])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
