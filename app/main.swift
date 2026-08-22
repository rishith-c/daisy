import Cocoa
import WebKit

/// Bridges `window.webkit.messageHandlers.daisy` to the adoption scanner.
///
/// The web layer cannot enumerate ~/.claude, ~/.codex or opencode.db, so the
/// shell does it and hands the JSON back. Runs off the main thread — scanning
/// three session stores takes ~200ms and must not stall the first paint — and
/// is read-only all the way down.
final class Bridge: NSObject, WKScriptMessageHandler {
    weak var webView: WKWebView?

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any],
              body["cmd"] as? String == "agents" else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let json = Bridge.scan()
            guard !json.isEmpty else { return }
            DispatchQueue.main.async {
                let escaped = json.data(using: .utf8).flatMap {
                    String(data: try! JSONSerialization.data(withJSONObject: [String(data: $0, encoding: .utf8)!],
                                                             options: []), encoding: .utf8)
                } ?? "[\"\"]"
                self?.webView?.evaluateJavaScript(
                    "window.__daisyAgents(\(escaped)[0])", completionHandler: nil)
            }
        }
    }

    /// `python3 -m agents.discover --json`, run against the copy of the package
    /// inside the bundle so the app does not depend on the checkout still
    /// existing at the path it was built from.
    static func scan() -> String {
        guard let res = Bundle.main.resourceURL else { return "" }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = ["python3", "-m", "agents.discover", "--json", "--limit", "8"]
        proc.currentDirectoryURL = res
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        do { try proc.run() } catch { return "" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 else { return "" }
        return String(data: data, encoding: .utf8) ?? ""
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var bridge: Bridge!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let rect = NSRect(x: 0, y: 0, width: 1320, height: 860)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = "Daisy"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.appearance = NSAppearance(named: .aqua)
        window.backgroundColor = .white
        window.minSize = NSSize(width: 900, height: 600)
        window.center()
        window.setFrameAutosaveName("DaisyMain")

        let config = WKWebViewConfiguration()
        let bridge = Bridge()
        config.userContentController.add(bridge, name: "daisy")
        let webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        bridge.webView = webView
        self.bridge = bridge
        webView.autoresizingMask = [.width, .height]
        webView.customUserAgent = (webView.value(forKey: "userAgent") as? String ?? "") + " DaisyNative"
        webView.setValue(false, forKey: "drawsBackground")

        if let url = Bundle.main.url(forResource: "index", withExtension: "html") {
            webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        }
        window.contentView?.addSubview(webView)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)

let mainMenu = NSMenu()
let appItem = NSMenuItem()
mainMenu.addItem(appItem)
let appMenu = NSMenu()
appMenu.addItem(NSMenuItem(title: "About Daisy", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: ""))
appMenu.addItem(NSMenuItem.separator())
appMenu.addItem(NSMenuItem(title: "Quit Daisy", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
appItem.submenu = appMenu

let editItem = NSMenuItem()
mainMenu.addItem(editItem)
let editMenu = NSMenu(title: "Edit")
editMenu.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
editItem.submenu = editMenu

app.mainMenu = mainMenu
let delegate = AppDelegate()
app.delegate = delegate
app.run()
