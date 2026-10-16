import AppKit
import CoreGraphics
import Foundation
import Vision

func fail(_ message: String) -> Never {
    fputs(message + "\n", stderr)
    exit(2)
}

guard CommandLine.arguments.count >= 3 else {
    fail("usage: macos_scene.swift ocr|color IMAGE [RED GREEN BLUE]")
}
let mode = CommandLine.arguments[1]
let path = CommandLine.arguments[2]
guard let image = NSImage(contentsOfFile: path) else { fail("cannot open screenshot") }
var proposed = CGRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &proposed, context: nil, hints: nil) else {
    fail("cannot decode screenshot")
}

if mode == "ocr" {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    do {
        try VNImageRequestHandler(cgImage: cgImage).perform([request])
    } catch {
        fail("Vision OCR failed: \(error)")
    }
    let text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    print(text)
} else if mode == "color" {
    guard CommandLine.arguments.count == 6,
          let red = UInt8(CommandLine.arguments[3]),
          let green = UInt8(CommandLine.arguments[4]),
          let blue = UInt8(CommandLine.arguments[5]) else {
        fail("color mode requires RGB bytes")
    }
    let width = cgImage.width
    let height = cgImage.height
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
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
        return true
    }
    guard rendered else { fail("cannot create screenshot bitmap") }
    var count = 0
    var minX = width
    var minY = height
    var maxX = -1
    var maxY = -1
    for y in 0..<height {
        let row = y * rowBytes
        for x in 0..<width {
            let offset = row + x * 4
            if abs(Int(pixels[offset]) - Int(red)) <= 3 &&
               abs(Int(pixels[offset + 1]) - Int(green)) <= 3 &&
               abs(Int(pixels[offset + 2]) - Int(blue)) <= 3 && pixels[offset + 3] >= 250 {
                count += 1
                minX = min(minX, x)
                minY = min(minY, y)
                maxX = max(maxX, x)
                maxY = max(maxY, y)
            }
        }
    }
    let record: [String: Any] = ["image_width": width, "image_height": height,
        "matching_pixels": count, "bounds": count == 0 ? NSNull() : ["x": minX, "y": minY,
        "width": maxX - minX + 1, "height": maxY - minY + 1] as [String: Int]]
    let data = try JSONSerialization.data(withJSONObject: record, options: [.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
} else {
    fail("unknown mode: \(mode)")
}
