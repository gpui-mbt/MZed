import CoreGraphics
import Foundation

guard CommandLine.arguments.count == 2, let pid = Int32(CommandLine.arguments[1]) else {
    fputs("usage: macos_window.swift PID\n", stderr)
    exit(2)
}

let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
let records: [[String: Any]] = windows.compactMap { window in
    guard (window[kCGWindowOwnerPID as String] as? Int32) == pid,
          (window[kCGWindowLayer as String] as? Int) == 0,
          let number = window[kCGWindowNumber as String] as? Int,
          let title = window[kCGWindowName as String] as? String,
          let bounds = window[kCGWindowBounds as String] as? [String: CGFloat],
          let x = bounds["X"], let y = bounds["Y"],
          let width = bounds["Width"], let height = bounds["Height"],
          width > 0, height > 0 else {
        return nil
    }
    return ["window_number": number, "title": title, "x": x, "y": y,
            "width": width, "height": height]
}
let data = try JSONSerialization.data(withJSONObject: records, options: [.sortedKeys])
print(String(decoding: data, as: UTF8.self))
