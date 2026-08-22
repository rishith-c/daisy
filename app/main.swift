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
    static let gardenOrigin = "https://garden-taupe-three.vercel.app"

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let command = body["cmd"] as? String else { return }
        switch command {
        case "agents":
            run(["python3", "-m", "agents.discover", "--json", "--limit", "8"], callback: "window.__daisyAgents")
        case "onboarding.agents":
            run(["python3", "labctl.py", "agents", "--json"], callback: "window.__daisyOnboarding")
        case "chain.status":
            run(["python3", "labctl.py", "chain", "--json"], callback: "window.__daisyChainStatus")
        case "chain.run":
            guard let raw = body["goal"] as? String else { return }
            let goal = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !goal.isEmpty, goal.count <= 12000 else {
                emit("window.__daisyChainRun", json: "{\"error\":\"Enter a goal between 1 and 12,000 characters.\"}")
                return
            }
            run(["python3", "labctl.py", "run", "--brief", goal, "--lane", "crew", "--daisy-chain", "--json"],
                callback: "window.__daisyChainRun")
        case "agent.run":
            guard let rawGoal = body["goal"] as? String,
                  let vendor = body["vendor"] as? String,
                  let model = body["model"] as? String else { return }
            let goal = rawGoal.trimmingCharacters(in: .whitespacesAndNewlines)
            let effort = body["effort"] as? String ?? ""
            let provider = body["provider"] as? String ?? ""
            let vendors = Set(["claude", "codex", "opencode"])
            let efforts = Set(["", "low", "light", "medium", "high", "xhigh", "max", "ultra"])
            let safeID = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._/:"))
            let modelSafe = !model.isEmpty && model.count <= 160 && model.unicodeScalars.allSatisfy(safeID.contains)
            let providerSafe = provider.count <= 80 && provider.unicodeScalars.allSatisfy(safeID.contains)
            guard !goal.isEmpty, goal.count <= 12000, vendors.contains(vendor),
                  efforts.contains(effort), modelSafe, providerSafe else {
                emit("window.__daisyAgentRun", json: "{\"ok\":false,\"reason\":\"The selected agent, model, or goal is invalid.\"}")
                return
            }
            run(["python3", "labctl.py", "agent", "--name", vendor, "--model", model,
                 "--effort", effort, "--provider", provider, "--prompt", goal, "--json"],
                callback: "window.__daisyAgentRun")
        case "app.reset":
            run(["python3", "-m", "garden.link", "unlink"], callback: "window.__daisyReset")
        case "garden", "garden.status":
            run(["python3", "-m", "garden.link", "status"], callback: "window.__daisyGardenStatus")
        case "garden.pair":
            guard let raw = body["code"] as? String else { return }
            let code = raw.filter { $0.isLetter || $0.isNumber }
            guard code.count == 6 else {
                emit("window.__daisyGardenPair", json: "{\"linked\":false,\"why\":\"Enter the six-character code from Garden.\"}")
                return
            }
            run(["python3", "-m", "garden.link", "pair", "--code", code], callback: "window.__daisyGardenPair")
        case "garden.autopublish":
            let enabled = body["on"] as? Bool == true
            run(["python3", "-m", "garden.link", "autopublish", enabled ? "--on" : "--off"], callback: "window.__daisyGardenStatus")
        case "garden.open":
            let requested = body["url"] as? String ?? Bridge.gardenOrigin + "/index"
            guard let url = URL(string: requested), url.scheme == "https",
                  url.host == "garden-taupe-three.vercel.app" else { return }
            NSWorkspace.shared.open(url)
        default:
            return
        }
    }

    /// Run a fixed argument array against the Python packages copied into the
    /// bundle. No command is ever interpreted by a shell.
    func run(_ arguments: [String], callback: String) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let json = Bridge.run(arguments: arguments)
            guard !json.isEmpty else { return }
            self?.emit(callback, json: json)
        }
    }

    static func run(arguments: [String]) -> String {
        guard let resources = Bundle.main.resourceURL else { return "" }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        proc.arguments = arguments
        proc.currentDirectoryURL = resources
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        do { try proc.run() } catch { return "" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        return String(data: data, encoding: .utf8) ?? ""
    }

    func emit(_ callback: String, json: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: [json]),
              let quoted = String(data: data, encoding: .utf8) else { return }
        DispatchQueue.main.async { [weak self] in
            self?.webView?.evaluateJavaScript("\(callback)(\(quoted)[0])", completionHandler: nil)
        }
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
