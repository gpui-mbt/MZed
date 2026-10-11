import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import ImageIO
import Vision

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
    guard !requireFrontmost || NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
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
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    // Cleanup is a release only and goes directly to the PID that received the
    // owned down. It never enters the global event stream or reactivates the app.
    event.postToPid(pid)
    emit(["pid": Int(pid), "cleanup": "targeted-mouse-up", "posted_to_target_pid": true])
}

func postCleanupKeyRelease(pid: pid_t, keyCode: Int, flags: CGEventFlags) {
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    guard (0...Int(UInt16.max)).contains(keyCode),
          let event = CGEvent(keyboardEventSource: nil, virtualKey: UInt16(keyCode), keyDown: false) else {
        fail("cannot create targeted key-up cleanup event for code \(keyCode)")
    }
    event.flags = flags
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    event.postToPid(pid)
    emit(["pid": Int(pid), "cleanup": "targeted-key-up", "posted_to_target_pid": true])
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
    if let error = cleanupGuardError(pid: pid) { fail(error) }
    event.postToPid(pid)
    emit(["pid": Int(pid), "cleanup": "targeted-text-key-up", "posted_to_target_pid": true])
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
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    event.post(tap: .cghidEventTap)
    emit(["pid": Int(pid), "window_id": Int(windowID), "event": action, "posted": true])
}

func postKeyEvent(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                  action: String, keyCode: Int, flags: CGEventFlags) {
    guard action == "down" || action == "up" else { fail("key action must be down or up") }
    guard (0...Int(UInt16.max)).contains(keyCode),
          let event = CGEvent(keyboardEventSource: nil, virtualKey: UInt16(keyCode), keyDown: action == "down") else {
        fail("cannot create key event for code \(keyCode)")
    }
    event.flags = flags
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    event.post(tap: .cghidEventTap)
    emit(["pid": Int(pid), "window_id": Int(windowID), "event": "key-\(action)", "posted": true])
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
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    event.post(tap: .cghidEventTap)
    emit(["pid": Int(pid), "window_id": Int(windowID), "event": "text-\(action)", "posted": true])
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

func consoleSessionFlags() -> [String: Any] {
    let session = CGSessionCopyCurrentDictionary() as? [String: Any] ?? [:]
    var result: [String: Any] = [:]
    if let locked = session["CGSSessionScreenIsLocked"] as? NSNumber {
        result["session_locked"] = locked.boolValue
    }
    if let onConsole = session["kCGSSessionOnConsoleKey"] as? NSNumber {
        result["on_console"] = onConsole.boolValue
    }
    return result
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
    fail("usage: macos_ui.swift permissions|windows PID|activate PID WINDOW_ID FIXTURE|capture PID WINDOW_ID FIXTURE PNG|mouse-event PID WINDOW_ID FIXTURE ACTION X Y|key-event PID WINDOW_ID FIXTURE ACTION CODE FLAGS|text-event PID WINDOW_ID FIXTURE ACTION TEXT|cleanup-mouse PID X Y|cleanup-key PID CODE FLAGS|cleanup-text PID TEXT|resize PID WINDOW_ID FIXTURE WIDTH HEIGHT|ocr PNG|colors PNG RED GREEN BLUE")
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
case "windows":
    guard arguments.count == 2 else { fail("windows requires PID") }
    let pid = integer(arguments[1], "PID")
    let frontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
    emit(["frontmost_pid": Int(frontmost), "windows": windowRecords().filter { ($0["pid"] as? Int) == pid }])
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
    usleep(250_000)
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let frontmostResult = AXUIElementSetAttributeValue(
        appElement, kAXFrontmostAttribute as CFString, kCFBooleanTrue
    )
    usleep(250_000)
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    let focusedWindowResult = AXUIElementSetAttributeValue(
        appElement, kAXFocusedWindowAttribute as CFString,
        accessibilityWindow(pid: pid, windowID: windowID, fixtureName: fixtureName)
    )
    usleep(250_000)
    emit([
        "pid": Int(pid),
        "window_id": Int(windowID),
        "app_activate_returned": appActivated,
        "ax_frontmost_error": frontmostResult.rawValue,
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
    guard arguments.count == 7 else { fail("key-event requires PID WINDOW_ID FIXTURE ACTION CODE FLAGS") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    postKeyEvent(pid: pid, windowID: windowID, fixtureName: arguments[3],
                 action: arguments[4], keyCode: integer(arguments[5], "key code"),
                 flags: eventFlags(arguments[6]))
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
