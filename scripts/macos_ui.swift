import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import ImageIO
import Vision

let keyboardSenderSettleRequestedMS = 100
let keyboardSenderSettleMinimumMS = 90

func fail(_ message: String) -> Never {
    fputs("macOS UI helper: \(message)\n", stderr)
    exit(2)
}

func emit(_ value: Any) {
    do {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .fragmentsAllowed])
        print(String(decoding: data, as: UTF8.self))
    } catch {
        fail("cannot encode JSON: \(error)")
    }
}

func number(_ value: String?, _ name: String) -> Double {
    guard let value, let parsed = Double(value), parsed.isFinite else {
        fail("\(name) must be a finite number")
    }
    return parsed
}

func integer(_ value: String?, _ name: String) -> Int {
    guard let value, let parsed = Int(value) else {
        fail("\(name) must be an integer")
    }
    return parsed
}

func windowRecords() -> [[String: Any]] {
    let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
    return windows.compactMap { window in
        guard let owner = (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
              let layer = (window[kCGWindowLayer as String] as? NSNumber)?.intValue,
              layer == 0,
              let number = (window[kCGWindowNumber as String] as? NSNumber)?.uint32Value,
              let bounds = window[kCGWindowBounds as String] as? [String: CGFloat],
              let x = bounds["X"], let y = bounds["Y"],
              let width = bounds["Width"], let height = bounds["Height"],
              width > 0, height > 0 else {
            return nil
        }
        let title = window[kCGWindowName as String] as? String ?? ""
        return [
            "pid": Int(owner),
            "window_id": Int(number),
            "title": title,
            "x": Double(x),
            "y": Double(y),
            "width": Double(width),
            "height": Double(height),
            "layer": layer,
            "visible_on_screen": true,
        ]
    }
}

func frontmostGuardAllows(targetPID: pid_t, currentFrontmostPID: pid_t?, required: Bool) -> Bool {
    !required || currentFrontmostPID == targetPID
}

func focusedWindowGuardAllows(expectedWindowID: Int?, currentWindowID: Int?) -> Bool {
    expectedWindowID == nil || expectedWindowID == currentWindowID
}

func axPressGuardAllows(targetPID: pid_t, focusedPID: pid_t?, expectedFocusedWindowID: Int,
                        currentFocusedWindowID: Int?, buttonPID: pid_t?,
                        expectedButtonTitle: String, actualButtonTitle: String?,
                        buttonEnabled: Bool, matchingButtonCount: Int,
                        traversalComplete: Bool) -> Bool {
    focusedPID == targetPID
        && focusedWindowGuardAllows(expectedWindowID: expectedFocusedWindowID,
                                    currentWindowID: currentFocusedWindowID)
        && buttonPID == targetPID
        && actualButtonTitle == expectedButtonTitle
        && buttonEnabled
        && matchingButtonCount == 1
        && traversalComplete
}

func eventGuardError(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                     requireFrontmost: Bool = true) -> String? {
    guard AXIsProcessTrusted() else {
        return "Accessibility permission is not granted to this terminal/helper"
    }
    let session = consoleSessionFlags()
    guard let locked = session["session_locked"] as? Bool, !locked else {
        return "console session is locked or its state is unavailable"
    }
    guard let onConsole = session["on_console"] as? Bool, onConsole else {
        return "invoking process is not on the foreground console"
    }
    guard NSRunningApplication(processIdentifier: pid) != nil else {
        return "target process PID \(pid) is no longer running"
    }
    let owned = windowRecords().filter { ($0["pid"] as? Int) == Int(pid) }
    let fixtureWindows = owned.filter { record in
        let title = record["title"] as? String ?? ""
        let width = record["width"] as? Double ?? 0
        let height = record["height"] as? Double ?? 0
        return title.localizedCaseInsensitiveContains(fixtureName) && width >= 700 && height >= 500
    }
    guard fixtureWindows.count == 1,
          (fixtureWindows[0]["window_id"] as? Int) == Int(windowID) else {
        return "selected visible fixture window \(windowID) is missing, ambiguous, or changed for PID \(pid)"
    }
    guard frontmostGuardAllows(targetPID: pid,
                               currentFrontmostPID: NSWorkspace.shared.frontmostApplication?.processIdentifier,
                               required: requireFrontmost) else {
        return "target application PID \(pid) is not frontmost; refusing UI interaction"
    }
    return nil
}

func requireEventTarget(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                        requireFrontmost: Bool = true) -> [String: Any] {
    if let error = eventGuardError(pid: pid, windowID: windowID,
                                   fixtureName: fixtureName, requireFrontmost: requireFrontmost) {
        fail(error)
    }
    guard let record = windowRecords().first(where: {
        ($0["pid"] as? Int) == Int(pid) && ($0["window_id"] as? Int) == Int(windowID)
    }) else {
        fail("selected visible fixture window \(windowID) disappeared for PID \(pid)")
    }
    return record
}

func waitForFrontmost(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                      timeout: TimeInterval) -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    repeat {
        if let error = eventGuardError(pid: pid, windowID: windowID,
                                       fixtureName: fixtureName, requireFrontmost: false) {
            fail(error)
        }
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid {
            return true
        }
        let remaining = deadline.timeIntervalSinceNow
        if remaining <= 0 { return false }
        // NSWorkspace's frontmostApplication value is updated through app
        // activation notifications. Service those notifications between
        // bounded checks; revalidate session and selected-window ownership on
        // the next iteration before accepting the refreshed foreground state.
        RunLoop.current.run(until: Date(timeIntervalSinceNow: min(0.05, remaining)))
    } while true
}

func captureWindow(pid: pid_t, windowID: CGWindowID, fixtureName: String, path: String) {
    guard CGPreflightScreenCaptureAccess() else {
        fail("Screen Recording permission is not granted to this terminal/helper")
    }
    let window = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let wx = window["x"] as! Double
    let wy = window["y"] as! Double
    let ww = window["width"] as! Double
    let wh = window["height"] as! Double
    // `screencapture -l` asks WindowServer for only this window. Never capture
    // the whole display and crop it: other applications can contain private data.
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    task.arguments = ["-x", "-o", "-l\(windowID)", path]
    let errors = Pipe()
    task.standardError = errors
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    do {
        try task.run()
        task.waitUntilExit()
    } catch {
        fail("cannot start window-only screencapture: \(error)")
    }
    guard task.terminationStatus == 0 else {
        let data = errors.fileHandleForReading.readDataToEndOfFile()
        let message = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        fail("window-only screencapture failed (\(task.terminationStatus)): \(message)")
    }
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        fail("screencapture did not create a readable window image at \(path)")
    }
    emit([
        "pid": Int(pid),
        "window_id": Int(windowID),
        "title": window["title"] ?? "",
        "x": wx,
        "y": wy,
        "width": ww,
        "height": wh,
        "pixel_width": image.width,
        "pixel_height": image.height,
        "scale_x": Double(CGFloat(image.width) / CGFloat(ww)),
        "scale_y": Double(CGFloat(image.height) / CGFloat(wh)),
        "path": path,
    ] as [String: Any])
}

func eventFlags(_ names: String) -> CGEventFlags {
    var flags = CGEventFlags()
    for name in names.split(separator: ",").map(String.init) where !name.isEmpty && name != "none" {
        switch name {
        case "command": flags.insert(.maskCommand)
        case "control": flags.insert(.maskControl)
        case "option": flags.insert(.maskAlternate)
        case "shift": flags.insert(.maskShift)
        default: fail("unknown keyboard modifier: \(name)")
        }
    }
    return flags
}

func resetUnmodifiedInputFlags(_ event: CGEvent) {
    // CGEvent sources can carry modifier state from an earlier synthetic
    // shortcut. Plain pointer and Unicode events must not inherit it.
    event.flags = CGEventFlags()
}

func validatePlainInputFlagFixtures() {
    var inherited = CGEventFlags()
    inherited.insert(.maskCommand)
    inherited.insert(.maskControl)

    guard let mouse = CGEvent(mouseEventSource: CGEventSource(stateID: .hidSystemState),
                              mouseType: .leftMouseDown, mouseCursorPosition: .zero,
                              mouseButton: .left) else {
        fail("cannot construct plain mouse flag fixture")
    }
    mouse.flags = inherited
    resetUnmodifiedInputFlags(mouse)
    guard mouse.flags.rawValue == 0 else { fail("plain mouse event retained inherited modifiers") }

    guard let text = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true) else {
        fail("cannot construct plain Unicode flag fixture")
    }
    text.flags = inherited
    resetUnmodifiedInputFlags(text)
    guard text.flags.rawValue == 0 else { fail("plain Unicode event retained inherited modifiers") }
}

func settleKeyboardSenderAfterPost() -> (requestedMilliseconds: Int, elapsedMilliseconds: Int) {
    let startedAt = ProcessInfo.processInfo.systemUptime
    let requiredSeconds = Double(keyboardSenderSettleRequestedMS) / 1000
    let deadline = startedAt + requiredSeconds
    let runLoop = RunLoop.current
    let completionTimer = Timer(
        fire: Date(timeIntervalSinceNow: requiredSeconds + 0.005),
        interval: 0,
        repeats: false
    ) { _ in }
    runLoop.add(completionTimer, forMode: .default)
    while ProcessInfo.processInfo.systemUptime < deadline {
        let remaining = deadline - ProcessInfo.processInfo.systemUptime
        _ = runLoop.run(mode: .default, before: Date(timeIntervalSinceNow: remaining))
    }
    withExtendedLifetime(completionTimer) {}
    let elapsedMilliseconds = Int((ProcessInfo.processInfo.systemUptime - startedAt) * 1000)
    guard elapsedMilliseconds >= keyboardSenderSettleMinimumMS else {
        fail("keyboard sender RunLoop settled for only \(elapsedMilliseconds)ms")
    }
    return (keyboardSenderSettleRequestedMS, elapsedMilliseconds)
}

func validateKeyboardSenderSettleFixture() -> (requestedMilliseconds: Int, elapsedMilliseconds: Int) {
    let settle = settleKeyboardSenderAfterPost()
    guard settle.elapsedMilliseconds >= keyboardSenderSettleMinimumMS else {
        fail("keyboard sender RunLoop fixture observed only \(settle.elapsedMilliseconds)ms")
    }
    return settle
}

func cleanupGuardError(pid: pid_t) -> String? {
    guard AXIsProcessTrusted() else {
        return "Accessibility permission was revoked before targeted release cleanup"
    }
    let session = consoleSessionFlags()
    guard let locked = session["session_locked"] as? Bool, !locked,
          let onConsole = session["on_console"] as? Bool, onConsole else {
        return "targeted release cleanup is unsafe while the console is locked or unavailable"
    }
    guard NSRunningApplication(processIdentifier: pid) != nil else {
        return "target process PID \(pid) exited before targeted release cleanup"
    }
    return nil
}

func postCleanupMouseRelease(pid: pid_t, x: Double, y: Double) {
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    guard let event = CGEvent(
        mouseEventSource: CGEventSource(stateID: .hidSystemState),
        mouseType: .leftMouseUp,
        mouseCursorPosition: CGPoint(x: x, y: y),
        mouseButton: .left
    ) else {
        fail("cannot create targeted mouse-up cleanup event")
    }
    resetUnmodifiedInputFlags(event)
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    guard event.flags.rawValue == 0 else { fail("targeted mouse-up cleanup retained modifier flags") }
    let flagsBeforePost = event.flags.rawValue
    // Cleanup is a release only and goes directly to the PID that received the
    // owned down. It never enters the global event stream or reactivates the app.
    event.postToPid(pid)
    emit(["pid": Int(pid), "cleanup": "targeted-mouse-up", "posted_to_target_pid": true,
          "event_flags_before_post_raw": flagsBeforePost,
          "event_flags_raw": flagsBeforePost,
          "event_flags_after_post_raw": event.flags.rawValue])
}

func postCleanupKeyRelease(pid: pid_t, keyCode: Int, flags: CGEventFlags) {
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    guard (0...Int(UInt16.max)).contains(keyCode),
          let event = CGEvent(keyboardEventSource: nil, virtualKey: UInt16(keyCode), keyDown: false) else {
        fail("cannot create targeted key-up cleanup event for code \(keyCode)")
    }
    event.flags = flags
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    let flagsBeforePost = event.flags.rawValue
    event.postToPid(pid)
    let senderSettle = settleKeyboardSenderAfterPost()
    emit(["pid": Int(pid), "cleanup": "targeted-key-up", "posted_to_target_pid": true,
          "event_flags_before_post_raw": flagsBeforePost,
          "event_flags_raw": flagsBeforePost,
          "event_flags_after_post_raw": event.flags.rawValue,
          "key_code": keyCode,
          "sender_runloop_settle_requested_ms": senderSettle.requestedMilliseconds,
          "sender_runloop_settle_ms": senderSettle.elapsedMilliseconds])
}

func postCleanupTextRelease(pid: pid_t, text: String) {
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    let units = Array(text.utf16)
    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
        fail("cannot create targeted Unicode key-up cleanup event")
    }
    units.withUnsafeBufferPointer { buffer in
        event.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
    }
    resetUnmodifiedInputFlags(event)
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    guard event.flags.rawValue == 0 else { fail("targeted Unicode cleanup retained modifier flags") }
    let flagsBeforePost = event.flags.rawValue
    event.postToPid(pid)
    let senderSettle = settleKeyboardSenderAfterPost()
    emit(["pid": Int(pid), "cleanup": "targeted-text-key-up", "posted_to_target_pid": true,
          "event_flags_before_post_raw": flagsBeforePost,
          "event_flags_raw": flagsBeforePost,
          "event_flags_after_post_raw": event.flags.rawValue,
          "key_code": 0,
          "unicode_utf16_units": units,
          "sender_runloop_settle_requested_ms": senderSettle.requestedMilliseconds,
          "sender_runloop_settle_ms": senderSettle.elapsedMilliseconds])
}

func postMouseEvent(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                    action: String, x: Double, y: Double) {
    let type: CGEventType
    switch action {
    case "down": type = .leftMouseDown
    case "up": type = .leftMouseUp
    case "move": type = .leftMouseDragged
    default:
        fail("mouse action must be down, move or up")
    }
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    guard let event = CGEvent(
        mouseEventSource: CGEventSource(stateID: .hidSystemState),
        mouseType: type,
        mouseCursorPosition: CGPoint(x: x, y: y),
        mouseButton: .left
    ) else {
        fail("cannot create mouse event")
    }
    resetUnmodifiedInputFlags(event)
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    guard event.flags.rawValue == 0 else { fail("plain mouse event retained modifier flags") }
    let flagsBeforePost = event.flags.rawValue
    event.post(tap: .cghidEventTap)
    emit(["pid": Int(pid), "window_id": Int(windowID), "event": action, "posted": true,
          "event_flags_before_post_raw": flagsBeforePost,
          "event_flags_raw": flagsBeforePost,
          "event_flags_after_post_raw": event.flags.rawValue])
}

func postKeyEvent(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                  action: String, keyCode: Int, flags: CGEventFlags,
                  expectedFocusedWindowID: Int? = nil) {
    guard action == "down" || action == "up" else { fail("key action must be down or up") }
    guard (0...Int(UInt16.max)).contains(keyCode),
          let event = CGEvent(keyboardEventSource: nil, virtualKey: UInt16(keyCode), keyDown: action == "down") else {
        fail("cannot create key event for code \(keyCode)")
    }
    event.flags = flags
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    if expectedFocusedWindowID != nil {
        let focusedID = focusedAXWindowID(pid: pid)
        guard focusedWindowGuardAllows(expectedWindowID: expectedFocusedWindowID,
                                       currentWindowID: focusedID) else {
            fail("focused Accessibility window changed before guarded key event: expected \(expectedFocusedWindowID!), got \(String(describing: focusedID))")
        }
    }
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let flagsBeforePost = event.flags.rawValue
    event.post(tap: .cghidEventTap)
    let senderSettle = settleKeyboardSenderAfterPost()
    var result: [String: Any] = ["pid": Int(pid), "window_id": Int(windowID),
                                "event": "key-\(action)", "posted": true,
                                "event_flags_before_post_raw": flagsBeforePost,
                                "event_flags_raw": flagsBeforePost,
                                "event_flags_after_post_raw": event.flags.rawValue,
                                "key_code": keyCode,
                                "sender_runloop_settle_requested_ms": senderSettle.requestedMilliseconds,
                                "sender_runloop_settle_ms": senderSettle.elapsedMilliseconds]
    if let expectedFocusedWindowID {
        result["expected_focused_window_id"] = expectedFocusedWindowID
    }
    emit(result)
}

func postTextEvent(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                   action: String, text: String) {
    guard action == "down" || action == "up" else { fail("text action must be down or up") }
    let units = Array(text.utf16)
    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: action == "down") else {
        fail("cannot create Unicode key event")
    }
    units.withUnsafeBufferPointer { buffer in
        event.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
    }
    resetUnmodifiedInputFlags(event)
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    guard event.flags.rawValue == 0 else { fail("plain Unicode event retained modifier flags") }
    let flagsBeforePost = event.flags.rawValue
    event.post(tap: .cghidEventTap)
    let senderSettle = settleKeyboardSenderAfterPost()
    emit(["pid": Int(pid), "window_id": Int(windowID), "event": "text-\(action)", "posted": true,
          "event_flags_before_post_raw": flagsBeforePost,
          "event_flags_raw": flagsBeforePost,
          "event_flags_after_post_raw": event.flags.rawValue,
          "key_code": 0,
          "unicode_utf16_units": units,
          "sender_runloop_settle_requested_ms": senderSettle.requestedMilliseconds,
          "sender_runloop_settle_ms": senderSettle.elapsedMilliseconds])
}

func accessibilityWindow(pid: pid_t, windowID: CGWindowID, fixtureName: String) -> AXUIElement {
    let application = AXUIElementCreateApplication(pid)
    var rawWindows: CFTypeRef?
    guard AXUIElementCopyAttributeValue(application, kAXWindowsAttribute as CFString, &rawWindows) == .success,
          let windows = rawWindows as? [AXUIElement] else {
        fail("Accessibility did not expose the target application's windows")
    }
    let record = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let expected = CGRect(
        x: record["x"] as! Double,
        y: record["y"] as! Double,
        width: record["width"] as! Double,
        height: record["height"] as! Double
    )
    let expectedTitle = record["title"] as? String ?? ""
    var titleMatches = [AXUIElement]()
    var geometryMatches = [AXUIElement]()
    for window in windows {
        var titleRaw: CFTypeRef?
        if AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &titleRaw) == .success,
           let title = titleRaw as? String, !expectedTitle.isEmpty, title == expectedTitle {
            titleMatches.append(window)
        }
        var positionRaw: CFTypeRef?
        var sizeRaw: CFTypeRef?
        guard AXUIElementCopyAttributeValue(window, kAXPositionAttribute as CFString, &positionRaw) == .success,
              AXUIElementCopyAttributeValue(window, kAXSizeAttribute as CFString, &sizeRaw) == .success,
              let positionRaw, let sizeRaw else { continue }
        let position = unsafeBitCast(positionRaw, to: AXValue.self)
        let size = unsafeBitCast(sizeRaw, to: AXValue.self)
        guard AXValueGetType(position) == .cgPoint, AXValueGetType(size) == .cgSize else { continue }
        var point = CGPoint.zero
        var dimensions = CGSize.zero
        guard AXValueGetValue(position, .cgPoint, &point), AXValueGetValue(size, .cgSize, &dimensions) else { continue }
        let actual = CGRect(origin: point, size: dimensions)
        let epsilon: CGFloat = 2
        if abs(actual.minX - expected.minX) <= epsilon && abs(actual.minY - expected.minY) <= epsilon &&
           abs(actual.width - expected.width) <= epsilon && abs(actual.height - expected.height) <= epsilon {
            geometryMatches.append(window)
        }
    }
    if titleMatches.count == 1 {
        return titleMatches[0]
    }
    if geometryMatches.count == 1 {
        return geometryMatches[0]
    }
    fail("Accessibility did not expose window \(windowID) with matching identity or geometry for PID \(pid)")
}

func axWindowBounds(_ element: AXUIElement) -> CGRect? {
    var positionRaw: CFTypeRef?
    var sizeRaw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &positionRaw) == .success,
          AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &sizeRaw) == .success,
          let positionRaw, let sizeRaw else { return nil }
    let position = unsafeBitCast(positionRaw, to: AXValue.self)
    let size = unsafeBitCast(sizeRaw, to: AXValue.self)
    guard AXValueGetType(position) == .cgPoint, AXValueGetType(size) == .cgSize else { return nil }
    var point = CGPoint.zero
    var dimensions = CGSize.zero
    guard AXValueGetValue(position, .cgPoint, &point),
          AXValueGetValue(size, .cgSize, &dimensions) else { return nil }
    return CGRect(origin: point, size: dimensions)
}

func axStringAttribute(_ element: AXUIElement, _ name: String) -> String? {
    var raw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &raw) == .success else { return nil }
    return raw as? String
}

func axElementPID(_ element: AXUIElement) -> pid_t? {
    var pid: pid_t = 0
    guard AXUIElementGetPid(element, &pid) == .success else { return nil }
    return pid
}

struct BoundedTreeTraversal<Node> {
    let nodes: [Node]
    let complete: Bool
}

func boundedDepthFirst<Node>(root: Node, maxNodes: Int = 512, maxDepth: Int = 8,
                             children: (Node) -> [Node]?) -> BoundedTreeTraversal<Node> {
    var stack: [(Node, Int)] = [(root, 0)]
    var nodes = [Node]()
    var complete = true
    while let (node, depth) = stack.popLast() {
        guard nodes.count < maxNodes else {
            complete = false
            break
        }
        nodes.append(node)
        guard let descendants = children(node) else {
            complete = false
            continue
        }
        if depth >= maxDepth {
            if !descendants.isEmpty {
                complete = false
            }
            continue
        }
        stack.append(contentsOf: descendants.map { ($0, depth + 1) })
    }
    return BoundedTreeTraversal(nodes: nodes, complete: complete)
}

// These AX roles are controls/text leaves for which AppKit commonly reports
// AXError.attributeUnsupported instead of an empty AXChildren array. Other
// roles must expose a readable child list before uniqueness can be claimed.
let axLeafRoles: Set<String> = [
    "AXButton", "AXCheckBox", "AXImage", "AXLink", "AXProgressIndicator",
    "AXRadioButton", "AXSeparator", "AXSlider", "AXStaticText", "AXTextArea",
    "AXTextField", "AXValueIndicator",
]

func axChildrenForScan(_ element: AXUIElement, targetPID: pid_t) -> [AXUIElement]? {
    guard axElementPID(element) == targetPID,
          let role = axStringAttribute(element, kAXRoleAttribute as String) else { return nil }
    var raw: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &raw)
    if result == .success {
        guard let children = raw as? [AXUIElement] else { return nil }
        guard children.allSatisfy({ axElementPID($0) == targetPID }) else { return nil }
        return children
    }
    if (result == .attributeUnsupported || result == .noValue) && axLeafRoles.contains(role) {
        return []
    }
    return nil
}

struct AXButtonCandidate {
    let element: AXUIElement
    let title: String?
    let enabled: Bool?
}

struct AXButtonScan {
    let candidates: [AXButtonCandidate]
    let complete: Bool
}

func axButtonScan(in root: AXUIElement, targetPID: pid_t) -> AXButtonScan {
    let traversal = boundedDepthFirst(root: root) { axChildrenForScan($0, targetPID: targetPID) }
    var complete = traversal.complete
    var candidates = [AXButtonCandidate]()
    for element in traversal.nodes {
        guard axElementPID(element) == targetPID,
              let role = axStringAttribute(element, kAXRoleAttribute as String) else {
            complete = false
            continue
        }
        guard role == (kAXButtonRole as String) else { continue }
        let title = axStringAttribute(element, kAXTitleAttribute as String)
        var enabledRaw: CFTypeRef?
        let enabled: Bool? = AXUIElementCopyAttributeValue(
            element, kAXEnabledAttribute as CFString, &enabledRaw) == .success
            ? enabledRaw as? Bool : nil
        if title == nil || enabled == nil {
            complete = false
        }
        candidates.append(AXButtonCandidate(element: element, title: title, enabled: enabled))
    }
    return AXButtonScan(candidates: candidates, complete: complete)
}

func axButtonRecords(_ scan: AXButtonScan) -> [[String: Any]] {
    scan.candidates.map { candidate in
        [
            "title": candidate.title as Any? ?? NSNull(),
            "role": kAXButtonRole as String,
            "enabled": candidate.enabled as Any? ?? NSNull(),
        ]
    }
}

func focusedAXWindow(pid: pid_t) -> AXUIElement? {
    let app = AXUIElementCreateApplication(pid)
    var raw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &raw) == .success,
          let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
    let focused = unsafeBitCast(raw, to: AXUIElement.self)
    guard axElementPID(focused) == pid else { return nil }
    return focused
}

func pressFocusedButton(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                        expectedFocusedWindowID: Int, buttonTitle: String) {
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    guard let focused = focusedAXWindow(pid: pid) else {
        fail("Accessibility did not expose a focused window owned by target PID \(pid)")
    }
    let processWindows = windowRecords().filter { ($0["pid"] as? Int) == Int(pid) }
    let focusedDescription = describeAXWindow(focused, processWindows: processWindows)
    guard focusedDescription["window_id"] as? Int == expectedFocusedWindowID else {
        fail("focused window identity changed: expected \(expectedFocusedWindowID), got \(focusedDescription)")
    }
    let scan = axButtonScan(in: focused, targetPID: pid)
    guard scan.complete else {
        fail("Accessibility button traversal was incomplete; refusing to claim \(buttonTitle.debugDescription) is unique")
    }
    let matches = scan.candidates.filter { $0.title == buttonTitle && $0.enabled == true }
    guard matches.count == 1 else {
        fail("expected exactly one enabled target-owned button named \(buttonTitle.debugDescription) in focused window; found \(matches.count)")
    }
    let button = matches[0].element
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let focusedID = focusedAXWindowID(pid: pid)
    let focusedPID = axElementPID(focused)
    let buttonPID = axElementPID(button)
    let actualTitle = axStringAttribute(button, kAXTitleAttribute as String)
    let buttonEnabled = axElementIsEnabled(button)
    guard axStringAttribute(button, kAXRoleAttribute as String) == (kAXButtonRole as String),
          axPressGuardAllows(targetPID: pid, focusedPID: focusedPID,
                             expectedFocusedWindowID: expectedFocusedWindowID,
                             currentFocusedWindowID: focusedID, buttonPID: buttonPID,
                             expectedButtonTitle: buttonTitle, actualButtonTitle: actualTitle,
                             buttonEnabled: buttonEnabled, matchingButtonCount: matches.count,
                             traversalComplete: scan.complete) else {
        fail("focused window or exact enabled button changed before Accessibility press")
    }
    let result = AXUIElementPerformAction(button, kAXPressAction as CFString)
    emit(["pid": Int(pid), "window_id": Int(windowID),
          "focused_window_id": expectedFocusedWindowID,
          "button_title": buttonTitle, "ax_press_error": result.rawValue])
}

func axElementIsEnabled(_ element: AXUIElement) -> Bool {
    var raw: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, kAXEnabledAttribute as CFString, &raw) == .success
        && (raw as? Bool == true)
}

func describeAXWindow(_ element: AXUIElement, processWindows: [[String: Any]]) -> [String: Any] {
    var detail: [String: Any] = [
        "title": axStringAttribute(element, kAXTitleAttribute as String) ?? "",
        "role": axStringAttribute(element, kAXRoleAttribute as String) ?? "",
        "subrole": axStringAttribute(element, kAXSubroleAttribute as String) ?? "",
    ]
    if let bounds = axWindowBounds(element) {
        detail["bounds"] = [
            "x": Double(bounds.origin.x), "y": Double(bounds.origin.y),
            "width": Double(bounds.width), "height": Double(bounds.height),
        ]
        let matches = processWindows.filter { record in
            guard let x = record["x"] as? Double, let y = record["y"] as? Double,
                  let width = record["width"] as? Double, let height = record["height"] as? Double else {
                return false
            }
            return abs(x - Double(bounds.origin.x)) <= 2
                && abs(y - Double(bounds.origin.y)) <= 2
                && abs(width - Double(bounds.width)) <= 2
                && abs(height - Double(bounds.height)) <= 2
        }
        if matches.count == 1, let windowID = matches[0]["window_id"] {
            detail["window_id"] = windowID
        }
    }
    return detail
}

func focusedAXWindowID(pid: pid_t) -> Int? {
    guard let focused = focusedAXWindow(pid: pid) else { return nil }
    let processWindows = windowRecords().filter { ($0["pid"] as? Int) == Int(pid) }
    return describeAXWindow(focused, processWindows: processWindows)["window_id"] as? Int
}

func axWindowState(pid: pid_t, windowID: CGWindowID, fixtureName: String) -> [String: Any] {
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let app = AXUIElementCreateApplication(pid)
    var windowsRaw: CFTypeRef?
    guard AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &windowsRaw) == .success,
          let windows = windowsRaw as? [AXUIElement] else {
        fail("Accessibility did not expose the target application's window list")
    }
    let processWindows = windowRecords().filter { ($0["pid"] as? Int) == Int(pid) }
    let selected = describeAXWindow(
        accessibilityWindow(pid: pid, windowID: windowID, fixtureName: fixtureName),
        processWindows: processWindows
    )
    guard selected["window_id"] as? Int == Int(windowID) else {
        fail("Accessibility selected window does not map to the requested WindowServer identity")
    }
    let focused = focusedAXWindow(pid: pid)
    let focusedButtons = focused.map { axButtonScan(in: $0, targetPID: pid) }
    func windowAttribute(_ name: String) -> Any {
        var raw: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, name as CFString, &raw) == .success,
              let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() else { return NSNull() }
        let element = unsafeBitCast(raw, to: AXUIElement.self)
        return describeAXWindow(element, processWindows: processWindows)
    }
    return [
        "pid": Int(pid),
        "selected_window_id": Int(windowID),
        "frontmost_pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1),
        "session": consoleSessionFlags(),
        "selected_window": selected,
        "focused_window": windowAttribute(kAXFocusedWindowAttribute as String),
        "main_window": windowAttribute(kAXMainWindowAttribute as String),
        "windows": windows.map { describeAXWindow($0, processWindows: processWindows) },
        "focused_buttons": focusedButtons.map(axButtonRecords) ?? [],
        "focused_buttons_complete": focusedButtons?.complete ?? false,
    ]
}

func sessionFlags(_ session: [String: Any]?) -> [String: Any] {
    guard let session else { return [:] }
    var result: [String: Any] = [:]
    let onConsole = session["kCGSSessionOnConsoleKey"] as? NSNumber
    if let lockValue = session["CGSSessionScreenIsLocked"] {
        guard let locked = lockValue as? NSNumber else {
            if let onConsole {
                result["on_console"] = onConsole.boolValue
            }
            return result
        }
        result["session_locked"] = locked.boolValue
    } else if onConsole?.boolValue == true {
        // On this macOS host the private lock marker is present with `true`
        // while locked, and omitted in an unlocked console session. Treat that
        // omission as unlocked only when the session dictionary explicitly
        // identifies the caller as on-console. A missing dictionary or console
        // marker, or an unrecognized lock value, remains unknown and therefore
        // fails closed in event guards.
        result["session_locked"] = false
    }
    if let onConsole {
        result["on_console"] = onConsole.boolValue
    }
    return result
}

func consoleSessionFlags() -> [String: Any] {
    sessionFlags(CGSessionCopyCurrentDictionary() as? [String: Any])
}

final class AXButtonFixtureNode {
    let role: String
    let title: String?
    let enabled: Bool?
    let children: [AXButtonFixtureNode]?

    init(role: String, title: String? = nil, enabled: Bool? = nil,
         children: [AXButtonFixtureNode]? = []) {
        self.role = role
        self.title = title
        self.enabled = enabled
        self.children = children
    }
}

func scanAXButtonFixture(_ root: AXButtonFixtureNode) -> (matches: Int, complete: Bool) {
    let traversal = boundedDepthFirst(root: root) { node -> [AXButtonFixtureNode]? in
        if let children = node.children {
            return children
        }
        return axLeafRoles.contains(node.role) ? [] : nil
    }
    var complete = traversal.complete
    var matches = 0
    for node in traversal.nodes where node.role == (kAXButtonRole as String) {
        guard let title = node.title, let enabled = node.enabled else {
            complete = false
            continue
        }
        if title == "No" && enabled {
            matches += 1
        }
    }
    return (matches, complete)
}

func validateAXButtonTraversalFixtures() {
    let oneButton = scanAXButtonFixture(AXButtonFixtureNode(
        role: "AXWindow", children: [AXButtonFixtureNode(role: "AXButton", title: "No", enabled: true)]
    ))
    guard oneButton.complete, oneButton.matches == 1 else {
        fail("one-button traversal fixture did not prove a unique enabled button")
    }

    let twoButtons = scanAXButtonFixture(AXButtonFixtureNode(
        role: "AXWindow", children: [
            AXButtonFixtureNode(role: "AXButton", title: "No", enabled: true),
            AXButtonFixtureNode(role: "AXButton", title: "No", enabled: true),
        ]
    ))
    guard twoButtons.complete, twoButtons.matches == 2 else {
        fail("two-button traversal fixture did not observe both enabled matches")
    }

    // Match the reviewed 512-node LIFO counterexample: the first No is left
    // pending after the later No and 510 leaf groups consume the scan budget.
    var breadthChildren = [AXButtonFixtureNode(role: "AXButton", title: "No", enabled: true)]
    breadthChildren.append(contentsOf: (0..<510).map { _ in AXButtonFixtureNode(role: "AXGroup", children: []) })
    breadthChildren.append(AXButtonFixtureNode(role: "AXButton", title: "No", enabled: true))
    let breadth = scanAXButtonFixture(AXButtonFixtureNode(role: "AXWindow", children: breadthChildren))
    guard !breadth.complete, breadth.matches == 1 else {
        fail("512-node breadth truncation fixture did not reject partial uniqueness")
    }

    var deepButton = AXButtonFixtureNode(role: "AXButton", title: "No", enabled: true)
    for _ in 0..<9 {
        deepButton = AXButtonFixtureNode(role: "AXGroup", children: [deepButton])
    }
    let depth = scanAXButtonFixture(AXButtonFixtureNode(role: "AXWindow", children: [
        deepButton,
        AXButtonFixtureNode(role: "AXButton", title: "No", enabled: true),
    ]))
    guard !depth.complete, depth.matches == 1 else {
        fail("depth truncation fixture did not reject partial uniqueness")
    }

    let unreadableContainer = scanAXButtonFixture(AXButtonFixtureNode(
        role: "AXWindow", children: [AXButtonFixtureNode(role: "AXGroup", children: nil)]
    ))
    guard !unreadableContainer.complete else {
        fail("unreadable container fixture was incorrectly treated as an empty subtree")
    }
    let routineLeaf = scanAXButtonFixture(AXButtonFixtureNode(
        role: "AXWindow", children: [AXButtonFixtureNode(role: "AXStaticText", children: nil)]
    ))
    guard routineLeaf.complete else {
        fail("routine leaf without AXChildren was incorrectly treated as an unreadable subtree")
    }
    let unreadableButton = scanAXButtonFixture(AXButtonFixtureNode(
        role: "AXWindow", children: [AXButtonFixtureNode(role: "AXButton", enabled: true)]
    ))
    guard !unreadableButton.complete else {
        fail("button with an unreadable title was treated as incapable of matching the exact action")
    }
    guard !axPressGuardAllows(targetPID: 123, focusedPID: 123, expectedFocusedWindowID: 19304,
                              currentFocusedWindowID: 19304, buttonPID: 123,
                              expectedButtonTitle: "No", actualButtonTitle: "No",
                              buttonEnabled: true, matchingButtonCount: 1,
                              traversalComplete: breadth.complete),
          !axPressGuardAllows(targetPID: 123, focusedPID: 123, expectedFocusedWindowID: 19304,
                              currentFocusedWindowID: 19304, buttonPID: 123,
                              expectedButtonTitle: "No", actualButtonTitle: "No",
                              buttonEnabled: true, matchingButtonCount: 1,
                              traversalComplete: depth.complete) else {
        fail("AXPress accepted a partial traversal with an unexamined possible duplicate")
    }
}

func validateSessionFlagFixtures() {
    let unlocked = sessionFlags(["kCGSSessionOnConsoleKey": NSNumber(value: true)])
    guard unlocked["session_locked"] as? Bool == false,
          unlocked["on_console"] as? Bool == true else {
        fail("unlocked on-console session fixture did not normalize to unlocked")
    }
    let locked = sessionFlags([
        "kCGSSessionOnConsoleKey": NSNumber(value: true),
        "CGSSessionScreenIsLocked": NSNumber(value: true),
    ])
    guard locked["session_locked"] as? Bool == true,
          locked["on_console"] as? Bool == true else {
        fail("explicitly locked session fixture did not remain locked")
    }
    let offConsole = sessionFlags(["kCGSSessionOnConsoleKey": NSNumber(value: false)])
    guard offConsole["session_locked"] == nil,
          offConsole["on_console"] as? Bool == false else {
        fail("off-console session fixture did not remain unavailable to event guards")
    }
    let empty = sessionFlags([:])
    guard empty.isEmpty else {
        fail("empty session dictionary fixture did not remain unavailable")
    }
    let malformedLock = sessionFlags([
        "kCGSSessionOnConsoleKey": NSNumber(value: true),
        "CGSSessionScreenIsLocked": "unknown",
    ])
    guard malformedLock["session_locked"] == nil,
          malformedLock["on_console"] as? Bool == true else {
        fail("unrecognized lock marker fixture did not remain unavailable")
    }
    guard sessionFlags(nil).isEmpty else {
        fail("missing session dictionary fixture did not remain unavailable")
    }
    guard frontmostGuardAllows(targetPID: 123, currentFrontmostPID: 999, required: false),
          !frontmostGuardAllows(targetPID: 123, currentFrontmostPID: 999, required: true),
          frontmostGuardAllows(targetPID: 123, currentFrontmostPID: 123, required: true) else {
        fail("activation and regular-event frontmost guard fixtures did not preserve their policies")
    }
    guard focusedWindowGuardAllows(expectedWindowID: nil, currentWindowID: nil),
          focusedWindowGuardAllows(expectedWindowID: 19304, currentWindowID: 19304),
          !focusedWindowGuardAllows(expectedWindowID: 19304, currentWindowID: 19303),
          !focusedWindowGuardAllows(expectedWindowID: 19304, currentWindowID: nil) else {
        fail("expected focused-window fixture did not reject changed or unknown AX focus")
    }
    guard axPressGuardAllows(targetPID: 123, focusedPID: 123, expectedFocusedWindowID: 19304,
                             currentFocusedWindowID: 19304, buttonPID: 123,
                             expectedButtonTitle: "No", actualButtonTitle: "No",
                             buttonEnabled: true, matchingButtonCount: 1,
                             traversalComplete: true),
          !axPressGuardAllows(targetPID: 123, focusedPID: 999, expectedFocusedWindowID: 19304,
                              currentFocusedWindowID: 19304, buttonPID: 123,
                              expectedButtonTitle: "No", actualButtonTitle: "No",
                              buttonEnabled: true, matchingButtonCount: 1,
                              traversalComplete: true),
          !axPressGuardAllows(targetPID: 123, focusedPID: 123, expectedFocusedWindowID: 19304,
                              currentFocusedWindowID: nil, buttonPID: 123,
                              expectedButtonTitle: "No", actualButtonTitle: "No",
                              buttonEnabled: true, matchingButtonCount: 1,
                              traversalComplete: true),
          !axPressGuardAllows(targetPID: 123, focusedPID: 123, expectedFocusedWindowID: 19304,
                              currentFocusedWindowID: 19304, buttonPID: 999,
                              expectedButtonTitle: "No", actualButtonTitle: "No",
                              buttonEnabled: true, matchingButtonCount: 1,
                              traversalComplete: true),
          !axPressGuardAllows(targetPID: 123, focusedPID: 123, expectedFocusedWindowID: 19304,
                              currentFocusedWindowID: 19304, buttonPID: 123,
                              expectedButtonTitle: "No", actualButtonTitle: "Yes",
                              buttonEnabled: true, matchingButtonCount: 1,
                              traversalComplete: true),
          !axPressGuardAllows(targetPID: 123, focusedPID: 123, expectedFocusedWindowID: 19304,
                              currentFocusedWindowID: 19304, buttonPID: 123,
                              expectedButtonTitle: "No", actualButtonTitle: "No",
                              buttonEnabled: false, matchingButtonCount: 1,
                              traversalComplete: true),
          !axPressGuardAllows(targetPID: 123, focusedPID: 123, expectedFocusedWindowID: 19304,
                              currentFocusedWindowID: 19304, buttonPID: 123,
                              expectedButtonTitle: "No", actualButtonTitle: "No",
                              buttonEnabled: true, matchingButtonCount: 2,
                              traversalComplete: true) else {
        fail("AXPress fixtures did not reject changed PID, focus, label, enabled state or ambiguity")
    }
}

func resizeWindow(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                  width: Double, height: Double) {
    let window = accessibilityWindow(pid: pid, windowID: windowID, fixtureName: fixtureName)
    var size = CGSize(width: width, height: height)
    guard let value = AXValueCreate(.cgSize, &size) else {
        fail("cannot create Accessibility window-size value")
    }
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let result = AXUIElementSetAttributeValue(window, kAXSizeAttribute as CFString, value)
    guard result == .success else {
        fail("Accessibility window resize returned AXError \(result.rawValue)")
    }
    emit(["pid": Int(pid), "window_id": Int(windowID), "width": width, "height": height])
}

func textObservations(_ path: String) -> [[String: Any]] {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        fail("cannot read screenshot at \(path)")
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    do {
        try VNImageRequestHandler(cgImage: image).perform([request])
    } catch {
        fail("Vision OCR failed: \(error)")
    }
    return (request.results ?? []).compactMap { observation in
        guard let text = observation.topCandidates(1).first?.string else { return nil }
        let box = observation.boundingBox
        return [
            "text": text,
            "x": Int((box.minX * CGFloat(image.width)).rounded()),
            "y": Int(((1 - box.maxY) * CGFloat(image.height)).rounded()),
            "width": Int((box.width * CGFloat(image.width)).rounded()),
            "height": Int((box.height * CGFloat(image.height)).rounded()),
        ]
    }
}

func colorComponents(_ path: String, red: UInt8, green: UInt8, blue: UInt8) -> [[String: Any]] {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        fail("cannot read screenshot at \(path)")
    }
    let width = image.width
    let height = image.height
    let rowBytes = width * 4
    var pixels = [UInt8](repeating: 0, count: rowBytes * height)
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
    let bitmapInfo = CGBitmapInfo.byteOrder32Big.union(CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue))
    let rendered = pixels.withUnsafeMutableBytes { bytes -> Bool in
        guard let context = CGContext(data: bytes.baseAddress, width: width, height: height,
                                      bitsPerComponent: 8, bytesPerRow: rowBytes,
                                      space: colorSpace, bitmapInfo: bitmapInfo.rawValue) else {
            return false
        }
        context.interpolationQuality = .none
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return true
    }
    guard rendered else { fail("cannot decode screenshot pixels") }

    var matches = [Int]()
    for y in 0..<height {
        let row = y * rowBytes
        for x in 0..<width {
            let offset = row + x * 4
            if abs(Int(pixels[offset]) - Int(red)) <= 3 &&
               abs(Int(pixels[offset + 1]) - Int(green)) <= 3 &&
               abs(Int(pixels[offset + 2]) - Int(blue)) <= 3 && pixels[offset + 3] >= 250 {
                matches.append(y * width + x)
            }
        }
    }
    guard matches.count <= 500_000 else {
        fail("color query matched too many pixels to form bounded UI evidence")
    }
    let matching = Set(matches)
    var visited = Set<Int>()
    var components = [[String: Any]]()
    for start in matches where !visited.contains(start) {
        var queue = [start]
        visited.insert(start)
        var head = 0
        var minX = width
        var minY = height
        var maxX = -1
        var maxY = -1
        while head < queue.count {
            let index = queue[head]
            head += 1
            let x = index % width
            let y = index / width
            minX = min(minX, x)
            minY = min(minY, y)
            maxX = max(maxX, x)
            maxY = max(maxY, y)
            let neighbors = [
                x > 0 ? index - 1 : -1,
                x + 1 < width ? index + 1 : -1,
                y > 0 ? index - width : -1,
                y + 1 < height ? index + width : -1,
            ]
            for neighbor in neighbors where neighbor >= 0 && matching.contains(neighbor) && visited.insert(neighbor).inserted {
                queue.append(neighbor)
            }
        }
        components.append([
            "pixels": queue.count,
            "x": minX,
            "y": minY,
            "width": maxX - minX + 1,
            "height": maxY - minY + 1,
        ])
    }
    return components
}

guard CommandLine.arguments.count >= 2 else {
    fail("usage: macos_ui.swift permissions|guard-fixtures|windows PID|ax-state PID WINDOW_ID FIXTURE|ax-press PID WINDOW_ID FIXTURE FOCUSED_WINDOW_ID BUTTON_TITLE|activate PID WINDOW_ID FIXTURE|capture PID WINDOW_ID FIXTURE PNG|mouse-event PID WINDOW_ID FIXTURE ACTION X Y|key-event PID WINDOW_ID FIXTURE ACTION CODE FLAGS|text-event PID WINDOW_ID FIXTURE ACTION TEXT|cleanup-mouse PID X Y|cleanup-key PID CODE FLAGS|cleanup-text PID TEXT|resize PID WINDOW_ID FIXTURE WIDTH HEIGHT|ocr PNG|colors PNG RED GREEN BLUE")
}

let arguments = Array(CommandLine.arguments.dropFirst())
switch arguments[0] {
case "permissions":
    var result: [String: Any] = [
        "accessibility": AXIsProcessTrusted(),
        "screen_recording": CGPreflightScreenCaptureAccess(),
    ]
    result.merge(consoleSessionFlags()) { _, new in new }
    emit(result)
case "guard-fixtures":
    validateSessionFlagFixtures()
    validateAXButtonTraversalFixtures()
    validatePlainInputFlagFixtures()
    let keyboardSenderSettle = validateKeyboardSenderSettleFixture()
    emit(["session_fixtures": "passed", "frontmost_guard_fixtures": "passed",
          "focused_window_guard_fixtures": "passed", "ax_press_guard_fixtures": "passed",
          "ax_traversal_fixtures": "passed", "plain_input_flags_fixtures": "passed",
          "keyboard_sender_settle_fixtures": "passed",
          "keyboard_sender_settle_requested_ms": keyboardSenderSettle.requestedMilliseconds,
          "keyboard_sender_settle_ms": keyboardSenderSettle.elapsedMilliseconds])
case "windows":
    guard arguments.count == 2 else { fail("windows requires PID") }
    let pid = integer(arguments[1], "PID")
    let frontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
    emit(["frontmost_pid": Int(frontmost), "windows": windowRecords().filter { ($0["pid"] as? Int) == pid }])
case "ax-state":
    guard arguments.count == 4 else { fail("ax-state requires PID WINDOW_ID FIXTURE") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    emit(axWindowState(pid: pid, windowID: windowID, fixtureName: arguments[3]))
case "ax-press":
    guard arguments.count == 6 else { fail("ax-press requires PID WINDOW_ID FIXTURE FOCUSED_WINDOW_ID BUTTON_TITLE") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    pressFocusedButton(pid: pid, windowID: windowID, fixtureName: arguments[3],
                       expectedFocusedWindowID: integer(arguments[4], "focused window ID"),
                       buttonTitle: arguments[5])
case "activate":
    guard arguments.count == 4 else { fail("activate requires PID WINDOW_ID FIXTURE") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    let fixtureName = arguments[3]
    // Activation is the one operation allowed to start while another app is
    // frontmost. It still requires an unlocked console and the unique visible
    // process-owned fixture window before it asks macOS to activate that PID.
    if let error = eventGuardError(pid: pid, windowID: windowID,
                                   fixtureName: fixtureName, requireFrontmost: false) {
        fail(error)
    }
    guard let application = NSRunningApplication(processIdentifier: pid) else {
        fail("cannot find running application PID \(pid)")
    }
    let appActivated = application.activate(options: [.activateAllWindows])
    let appElement = AXUIElementCreateApplication(pid)
    var frontmostResult: Any = "not_needed"
    if !waitForFrontmost(pid: pid, windowID: windowID,
                         fixtureName: fixtureName, timeout: 2.0) {
        // NSRunningApplication activation may be asynchronous or rejected.
        // Only the explicit activation path may request AX frontmost while a
        // different app is frontmost; session and selected-window ownership
        // are rechecked immediately before that mutation.
        _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName,
                               requireFrontmost: false)
        frontmostResult = AXUIElementSetAttributeValue(
            appElement, kAXFrontmostAttribute as CFString, kCFBooleanTrue
        ).rawValue
        if !waitForFrontmost(pid: pid, windowID: windowID,
                             fixtureName: fixtureName, timeout: 2.0) {
            emit([
                "pid": Int(pid),
                "window_id": Int(windowID),
                "activation_completed": false,
                "activation_stage": "AXFrontmost",
                "app_activate_returned": appActivated,
                "activation_policy": application.activationPolicy.rawValue,
                "app_is_active": application.isActive,
                "ax_frontmost_error": frontmostResult,
                "frontmost_pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1),
            ])
            exit(0)
        }
    }
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let focusedWindowResult = AXUIElementSetAttributeValue(
        appElement, kAXFocusedWindowAttribute as CFString,
        accessibilityWindow(pid: pid, windowID: windowID, fixtureName: fixtureName)
    )
    if !waitForFrontmost(pid: pid, windowID: windowID,
                         fixtureName: fixtureName, timeout: 2.0) {
        emit([
            "pid": Int(pid),
            "window_id": Int(windowID),
            "activation_completed": false,
            "activation_stage": "focused-window",
            "app_activate_returned": appActivated,
            "activation_policy": application.activationPolicy.rawValue,
            "app_is_active": application.isActive,
            "ax_frontmost_error": frontmostResult,
            "ax_focused_window_error": focusedWindowResult.rawValue,
            "frontmost_pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1),
        ])
        exit(0)
    }
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    emit([
        "pid": Int(pid),
        "window_id": Int(windowID),
        "activation_completed": true,
        "activation_stage": "complete",
        "app_activate_returned": appActivated,
        "activation_policy": application.activationPolicy.rawValue,
        "app_is_active": application.isActive,
        "ax_frontmost_error": frontmostResult,
        "ax_focused_window_error": focusedWindowResult.rawValue,
        "is_active": application.isActive,
        "frontmost_pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1),
    ])
case "capture":
    guard arguments.count == 5 else { fail("capture requires PID WINDOW_ID FIXTURE PNG") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    captureWindow(pid: pid, windowID: windowID, fixtureName: arguments[3], path: arguments[4])
case "mouse-event":
    guard arguments.count == 7 else { fail("mouse-event requires PID WINDOW_ID FIXTURE ACTION X Y") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    postMouseEvent(pid: pid, windowID: windowID, fixtureName: arguments[3],
                   action: arguments[4], x: number(arguments[5], "X"), y: number(arguments[6], "Y"))
case "key-event":
    guard arguments.count == 7 || arguments.count == 8 else { fail("key-event requires PID WINDOW_ID FIXTURE ACTION CODE FLAGS [EXPECTED_FOCUSED_WINDOW_ID]") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    postKeyEvent(pid: pid, windowID: windowID, fixtureName: arguments[3],
                 action: arguments[4], keyCode: integer(arguments[5], "key code"),
                 flags: eventFlags(arguments[6]),
                 expectedFocusedWindowID: arguments.count == 8 ? integer(arguments[7], "expected focused window ID") : nil)
case "text-event":
    guard arguments.count == 6 else { fail("text-event requires PID WINDOW_ID FIXTURE ACTION TEXT") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    postTextEvent(pid: pid, windowID: windowID, fixtureName: arguments[3],
                  action: arguments[4], text: arguments[5])
case "cleanup-mouse":
    guard arguments.count == 4 else { fail("cleanup-mouse requires PID X Y") }
    postCleanupMouseRelease(pid: pid_t(integer(arguments[1], "PID")),
                            x: number(arguments[2], "X"), y: number(arguments[3], "Y"))
case "cleanup-key":
    guard arguments.count == 4 else { fail("cleanup-key requires PID CODE FLAGS") }
    postCleanupKeyRelease(pid: pid_t(integer(arguments[1], "PID")),
                          keyCode: integer(arguments[2], "key code"), flags: eventFlags(arguments[3]))
case "cleanup-text":
    guard arguments.count == 3 else { fail("cleanup-text requires PID TEXT") }
    postCleanupTextRelease(pid: pid_t(integer(arguments[1], "PID")), text: arguments[2])
case "resize":
    guard arguments.count == 6 else { fail("resize requires PID WINDOW_ID FIXTURE WIDTH HEIGHT") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    let width = number(arguments[4], "width")
    let height = number(arguments[5], "height")
    resizeWindow(pid: pid, windowID: windowID, fixtureName: arguments[3], width: width, height: height)
case "ocr":
    guard arguments.count == 2 else { fail("ocr requires PNG") }
    emit(["observations": textObservations(arguments[1])])
case "colors":
    guard arguments.count == 5 else { fail("colors requires PNG RED GREEN BLUE") }
    guard let red = UInt8(arguments[2]), let green = UInt8(arguments[3]), let blue = UInt8(arguments[4]) else {
        fail("color channels must be bytes")
    }
    emit(["components": colorComponents(arguments[1], red: red, green: green, blue: blue)])
default:
    fail("unknown command: \(arguments[0])")
}
