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

func targetWindow(pid: pid_t, windowID: CGWindowID) -> [String: Any] {
    guard let record = windowRecords().first(where: {
        ($0["pid"] as? Int) == Int(pid) && ($0["window_id"] as? Int) == Int(windowID)
    }) else {
        fail("window \(windowID) is not a visible level-zero window owned by PID \(pid)")
    }
    return record
}

func requireFrontmost(pid: pid_t) {
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
        fail("target application PID \(pid) is not frontmost; refusing UI interaction")
    }
}

func requireAccessibility() {
    guard AXIsProcessTrusted() else {
        fail("Accessibility permission is not granted to this terminal/helper")
    }
}

func captureWindow(pid: pid_t, windowID: CGWindowID, path: String) {
    guard CGPreflightScreenCaptureAccess() else {
        fail("Screen Recording permission is not granted to this terminal/helper")
    }
    let window = targetWindow(pid: pid, windowID: windowID)
    requireFrontmost(pid: pid)
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

func postMouse(_ type: CGEventType, pid: pid_t, x: Double, y: Double) {
    requireAccessibility()
    requireFrontmost(pid: pid)
    guard let event = CGEvent(
        mouseEventSource: CGEventSource(stateID: .hidSystemState),
        mouseType: type,
        mouseCursorPosition: CGPoint(x: x, y: y),
        mouseButton: .left
    ) else {
        fail("cannot create mouse event")
    }
    event.post(tap: .cghidEventTap)
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

func postKey(_ keyCode: Int, flags: CGEventFlags, pid: pid_t) {
    requireAccessibility()
    requireFrontmost(pid: pid)
    guard (0...Int(UInt16.max)).contains(keyCode),
          let down = CGEvent(keyboardEventSource: nil, virtualKey: UInt16(keyCode), keyDown: true),
          let up = CGEvent(keyboardEventSource: nil, virtualKey: UInt16(keyCode), keyDown: false) else {
        fail("cannot create key event for code \(keyCode)")
    }
    down.flags = flags
    up.flags = flags
    down.post(tap: .cghidEventTap)
    usleep(30_000)
    up.post(tap: .cghidEventTap)
}

func postText(_ text: String, pid: pid_t) {
    requireAccessibility()
    requireFrontmost(pid: pid)
    for character in text {
        let units = Array(String(character).utf16)
        guard let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
              let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) else {
            fail("cannot create Unicode key event")
        }
        units.withUnsafeBufferPointer { buffer in
            down.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
            up.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
        }
        down.post(tap: .cghidEventTap)
        usleep(12_000)
        up.post(tap: .cghidEventTap)
        usleep(12_000)
    }
}

func accessibilityWindow(pid: pid_t, windowID: CGWindowID) -> AXUIElement {
    let application = AXUIElementCreateApplication(pid)
    var rawWindows: CFTypeRef?
    guard AXUIElementCopyAttributeValue(application, kAXWindowsAttribute as CFString, &rawWindows) == .success,
          let windows = rawWindows as? [AXUIElement] else {
        fail("Accessibility did not expose the target application's windows")
    }
    let record = targetWindow(pid: pid, windowID: windowID)
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

func resizeWindow(pid: pid_t, windowID: CGWindowID, width: Double, height: Double) {
    requireAccessibility()
    requireFrontmost(pid: pid)
    let window = accessibilityWindow(pid: pid, windowID: windowID)
    var size = CGSize(width: width, height: height)
    guard let value = AXValueCreate(.cgSize, &size) else {
        fail("cannot create Accessibility window-size value")
    }
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
    fail("usage: macos_ui.swift permissions|windows PID|activate PID WINDOW_ID|capture PID WINDOW_ID PNG|click PID X Y|down PID X Y|move PID X Y|up PID X Y|key PID CODE FLAGS|type PID TEXT|resize PID WINDOW_ID WIDTH HEIGHT|ocr PNG|colors PNG RED GREEN BLUE")
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
    guard arguments.count == 3 else { fail("activate requires PID WINDOW_ID") }
    requireAccessibility()
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    guard let application = NSRunningApplication(processIdentifier: pid) else {
        fail("cannot find running application PID \(pid)")
    }
    let appActivated = application.activate(options: [.activateAllWindows])
    let appElement = AXUIElementCreateApplication(pid)
    let frontmostResult = AXUIElementSetAttributeValue(
        appElement, kAXFrontmostAttribute as CFString, kCFBooleanTrue
    )
    let focusedWindowResult = AXUIElementSetAttributeValue(
        appElement, kAXFocusedWindowAttribute as CFString, accessibilityWindow(pid: pid, windowID: windowID)
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
    guard arguments.count == 4 else { fail("capture requires PID WINDOW_ID PNG") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    captureWindow(pid: pid, windowID: windowID, path: arguments[3])
case "click":
    guard arguments.count == 4 else { fail("click requires PID X Y") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let x = number(arguments[2], "X")
    let y = number(arguments[3], "Y")
    postMouse(.leftMouseDown, pid: pid, x: x, y: y)
    usleep(60_000)
    postMouse(.leftMouseUp, pid: pid, x: x, y: y)
    emit(["pid": Int(pid), "x": x, "y": y, "posted": true])
case "down", "move", "up":
    guard arguments.count == 4 else { fail("mouse event requires PID X Y") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let x = number(arguments[2], "X")
    let y = number(arguments[3], "Y")
    let type: CGEventType = arguments[0] == "down" ? .leftMouseDown : (arguments[0] == "up" ? .leftMouseUp : .leftMouseDragged)
    postMouse(type, pid: pid, x: x, y: y)
    emit(["pid": Int(pid), "x": x, "y": y, "event": arguments[0], "posted": true])
case "key":
    guard arguments.count == 4 else { fail("key requires PID CODE FLAGS") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let code = integer(arguments[2], "key code")
    postKey(code, flags: eventFlags(arguments[3]), pid: pid)
    emit(["pid": Int(pid), "key_code": code, "flags": arguments[3], "posted": true])
case "type":
    guard arguments.count == 3 else { fail("type requires PID and one text argument") }
    let pid = pid_t(integer(arguments[1], "PID"))
    postText(arguments[2], pid: pid)
    emit(["pid": Int(pid), "characters": arguments[2].count, "posted": true])
case "resize":
    guard arguments.count == 5 else { fail("resize requires PID WINDOW_ID WIDTH HEIGHT") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    let width = number(arguments[3], "width")
    let height = number(arguments[4], "height")
    resizeWindow(pid: pid, windowID: windowID, width: width, height: height)
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
