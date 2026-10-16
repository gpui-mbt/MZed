import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import Foundation
import ImageIO
import Vision

let inputSenderSettleRequestedMS = 100
let inputSenderSettleMinimumMS = 90

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

// CGDisplayBounds and CGWindowListCopyWindowInfo bounds use the same global
// CoreGraphics display coordinate space; the caller can therefore prove an AX
// resize stays on the display that contains the fixture window origin.
func displayRecords(pid: pid_t, windowID: CGWindowID, fixtureName: String) -> [[String: Any]] {
    _ = requireEventTarget(pid: pid, windowID: windowID, fixtureName: fixtureName)
    var displayCount: UInt32 = 0
    let countStatus = CGGetActiveDisplayList(0, nil, &displayCount)
    guard countStatus == .success, displayCount > 0 else {
        fail("CoreGraphics did not return active displays (\(countStatus.rawValue))")
    }
    let capacity = displayCount
    var displayIDs = [CGDirectDisplayID](repeating: 0, count: Int(capacity))
    let listStatus = displayIDs.withUnsafeMutableBufferPointer { buffer in
        guard let baseAddress = buffer.baseAddress else {
            fail("cannot allocate active display identifiers")
        }
        return CGGetActiveDisplayList(capacity, baseAddress, &displayCount)
    }
    guard listStatus == .success, displayCount > 0 else {
        fail("CoreGraphics could not enumerate active displays (\(listStatus.rawValue))")
    }
    return displayIDs.prefix(Int(displayCount)).compactMap { displayID in
        let bounds = CGDisplayBounds(displayID)
        guard bounds.origin.x.isFinite, bounds.origin.y.isFinite,
              bounds.width.isFinite, bounds.height.isFinite,
              bounds.width > 0, bounds.height > 0 else { return nil }
        return [
            "display_id": Int(displayID),
            "x": Double(bounds.origin.x),
            "y": Double(bounds.origin.y),
            "width": Double(bounds.width),
            "height": Double(bounds.height),
        ]
    }.sorted {
        ($0["display_id"] as? Int ?? 0) < ($1["display_id"] as? Int ?? 0)
    }
}

func frontmostGuardAllows(targetPID: pid_t, currentFrontmostPID: pid_t?, required: Bool) -> Bool {
    !required || currentFrontmostPID == targetPID
}

func focusedWindowGuardAllows(expectedWindowID: Int?, currentWindowID: Int?) -> Bool {
    expectedWindowID == nil || expectedWindowID == currentWindowID
}

enum AXPointRead<Value> {
    case value(Value)
    case axError(AXError)
    case malformed
    case budgetExceeded
}

struct AXPointWindowMapping {
    let available: Bool
    let budgetExceeded: Bool
    let matchingWindowIDs: [CGWindowID]

    var uniqueWindowID: CGWindowID? {
        available && matchingWindowIDs.count == 1 ? matchingWindowIDs[0] : nil
    }
}

struct AXPointQueries<Element> {
    let hitAtPoint: () -> AXPointRead<Element>
    let processID: (Element) -> AXPointRead<pid_t>
    let role: (Element) -> AXPointRead<String>
    let containingWindow: (Element) -> AXPointRead<Element>
    let parent: (Element) -> AXPointRead<Element>
    let geometry: (Element) -> AXPointRead<CGRect>
    let mapGeometry: (pid_t, CGRect) -> AXPointWindowMapping
    let sameElement: (Element, Element) -> Bool
}

struct AXPointResolutionFault: Error {
    let code: String
    let stage: String
    let axError: Int?
    let hitPID: pid_t?
    let hitRole: String?
    let parentDepth: Int?
    let matchingWindowIDs: [CGWindowID]?
    let candidateGeometry: CGRect?

    var jsonValue: [String: Any] {
        var result: [String: Any] = ["code": code, "stage": stage]
        if let axError { result["ax_error"] = axError }
        if let hitPID { result["hit_pid"] = Int(hitPID) }
        if let hitRole { result["hit_role"] = hitRole }
        if let parentDepth { result["parent_depth"] = parentDepth }
        if let matchingWindowIDs {
            result["matching_window_ids"] = matchingWindowIDs.map(Int.init)
        }
        if let candidateGeometry { result["candidate_geometry"] = frameJSON(candidateGeometry) }
        return result
    }
}

struct AXPointOwnership {
    let hitPID: pid_t
    let hitRole: String
    let resolutionOrigin: String
    let parentDepth: Int
    let parentResolutionComplete: Bool
    let candidatePID: pid_t
    let candidateWindowID: CGWindowID
    let candidateRole: String
    let candidateGeometry: CGRect
    let matchingWindowIDs: [CGWindowID]
    let globalPoint: CGPoint

    func matchesOriginal(targetPID: pid_t, expectedWindowID: CGWindowID,
                         point: CGPoint, currentWindowFrame: CGRect) -> Bool {
        candidatePID == targetPID && hitPID == targetPID
            && candidateWindowID == expectedWindowID
            && candidateRole == (kAXWindowRole as String)
            && parentResolutionComplete && parentDepth >= 0 && parentDepth <= 8
            && matchingWindowIDs == [expectedWindowID]
            && globalPoint.x.isFinite && globalPoint.y.isFinite
            && abs(globalPoint.x - point.x) <= 0.000001
            && abs(globalPoint.y - point.y) <= 0.000001
            && finiteFrame(candidateGeometry) && finiteFrame(currentWindowFrame)
            && abs(candidateGeometry.minX - currentWindowFrame.minX) <= 2
            && abs(candidateGeometry.minY - currentWindowFrame.minY) <= 2
            && abs(candidateGeometry.width - currentWindowFrame.width) <= 2
            && abs(candidateGeometry.height - currentWindowFrame.height) <= 2
            && candidateGeometry.contains(point)
            && currentWindowFrame.contains(point)
    }

    var jsonValue: [String: Any] {
        [
            "method": "accessibility_point_hit",
            "hit_pid": Int(hitPID),
            "hit_role": hitRole,
            "resolution_origin": resolutionOrigin,
            "parent_depth": parentDepth,
            "parent_resolution_complete": parentResolutionComplete,
            "candidate_pid": Int(candidatePID),
            "candidate_window_id": Int(candidateWindowID),
            "candidate_role": candidateRole,
            "candidate_geometry": frameJSON(candidateGeometry),
            "matching_window_ids": matchingWindowIDs.map(Int.init),
            "global_point": ["x": Double(globalPoint.x), "y": Double(globalPoint.y)],
        ]
    }
}

func finiteFrame(_ frame: CGRect) -> Bool {
    frame.origin.x.isFinite && frame.origin.y.isFinite
        && frame.width.isFinite && frame.height.isFinite
        && frame.width > 0 && frame.height > 0
}

final class AXCleanupBudget {
    private let deadline: TimeInterval
    private let now: () -> TimeInterval

    init() {
        let started = ProcessInfo.processInfo.systemUptime
        deadline = started + 2.0
        now = { ProcessInfo.processInfo.systemUptime }
        let systemWide = AXUIElementCreateSystemWide()
        let timeoutResult = AXUIElementSetMessagingTimeout(systemWide, 0.25)
        guard timeoutResult == .success else {
            fail("cannot set cleanup-helper Accessibility messaging timeout (\(timeoutResult.rawValue))")
        }
    }

    init(until deadline: TimeInterval, now: @escaping () -> TimeInterval) {
        self.deadline = deadline
        self.now = now
    }

    var hasTime: Bool { now() < deadline }
}

func axElementValue(_ result: AXError, _ raw: CFTypeRef?, budget: AXCleanupBudget)
    -> AXPointRead<CFTypeRef> {
    guard budget.hasTime else { return .budgetExceeded }
    guard result == .success else { return .axError(result) }
    guard let raw else { return .malformed }
    guard budget.hasTime else { return .budgetExceeded }
    return .value(raw)
}

func copyAXAttributeValue(budget: AXCleanupBudget,
                           copy: (inout CFTypeRef?) -> AXError) -> AXPointRead<CFTypeRef> {
    guard budget.hasTime else { return .budgetExceeded }
    var raw: CFTypeRef?
    let result = copy(&raw)
    return axElementValue(result, raw, budget: budget)
}

func axElementAttribute(_ element: AXUIElement, _ name: String,
                        budget: AXCleanupBudget) -> AXPointRead<AXUIElement> {
    switch copyAXAttributeValue(budget: budget, copy: { raw in
        AXUIElementCopyAttributeValue(element, name as CFString, &raw)
    }) {
    case .value(let value):
        guard CFGetTypeID(value) == AXUIElementGetTypeID() else { return .malformed }
        return .value(unsafeBitCast(value, to: AXUIElement.self))
    case .axError(let error): return .axError(error)
    case .malformed: return .malformed
    case .budgetExceeded: return .budgetExceeded
    }
}

func axStringValue(_ element: AXUIElement, _ name: String,
                   budget: AXCleanupBudget) -> AXPointRead<String> {
    switch copyAXAttributeValue(budget: budget, copy: { raw in
        AXUIElementCopyAttributeValue(element, name as CFString, &raw)
    }) {
    case .value(let value):
        guard CFGetTypeID(value) == CFStringGetTypeID(),
              let string = value as? String, !string.isEmpty else { return .malformed }
        return .value(string)
    case .axError(let error): return .axError(error)
    case .malformed: return .malformed
    case .budgetExceeded: return .budgetExceeded
    }
}

func axProcessID(_ element: AXUIElement, budget: AXCleanupBudget) -> AXPointRead<pid_t> {
    guard budget.hasTime else { return .budgetExceeded }
    var pid: pid_t = 0
    let result = AXUIElementGetPid(element, &pid)
    guard budget.hasTime else { return .budgetExceeded }
    guard result == .success, pid > 0 else {
        return result == .success ? .malformed : .axError(result)
    }
    return .value(pid)
}

func axFrame(_ element: AXUIElement, budget: AXCleanupBudget) -> AXPointRead<CGRect> {
    func value(_ name: String) -> AXPointRead<AXValue> {
        switch copyAXAttributeValue(budget: budget, copy: { raw in
            AXUIElementCopyAttributeValue(element, name as CFString, &raw)
        }) {
        case .value(let rawValue):
            guard CFGetTypeID(rawValue) == AXValueGetTypeID() else { return .malformed }
            return .value(unsafeBitCast(rawValue, to: AXValue.self))
        case .axError(let error): return .axError(error)
        case .malformed: return .malformed
        case .budgetExceeded: return .budgetExceeded
        }
    }
    let position: AXValue
    switch value(kAXPositionAttribute as String) {
    case .value(let result): position = result
    case .axError(let error): return .axError(error)
    case .malformed: return .malformed
    case .budgetExceeded: return .budgetExceeded
    }
    let size: AXValue
    switch value(kAXSizeAttribute as String) {
    case .value(let result): size = result
    case .axError(let error): return .axError(error)
    case .malformed: return .malformed
    case .budgetExceeded: return .budgetExceeded
    }
    guard AXValueGetType(position) == .cgPoint, AXValueGetType(size) == .cgSize else {
        return .malformed
    }
    var origin = CGPoint.zero
    var dimensions = CGSize.zero
    guard AXValueGetValue(position, .cgPoint, &origin),
          AXValueGetValue(size, .cgSize, &dimensions) else { return .malformed }
    let frame = CGRect(origin: origin, size: dimensions)
    return finiteFrame(frame) ? .value(frame) : .malformed
}

func mapAXGeometryToWindow(targetPID: pid_t, geometry: CGRect,
                           budget: AXCleanupBudget) -> AXPointWindowMapping {
    guard budget.hasTime else {
        return AXPointWindowMapping(available: false, budgetExceeded: true, matchingWindowIDs: [])
    }
    guard let rawWindows = CGWindowListCopyWindowInfo(
        [.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID
    ) as? [[String: Any]] else {
        return AXPointWindowMapping(available: false, budgetExceeded: false, matchingWindowIDs: [])
    }
    var matches = [CGWindowID]()
    for window in rawWindows {
        guard let ownerNumber = window[kCGWindowOwnerPID as String] as? NSNumber else {
            return AXPointWindowMapping(available: false, budgetExceeded: false, matchingWindowIDs: [])
        }
        let ownerPID = ownerNumber.int32Value
        guard ownerPID == targetPID else { continue }
        guard let layer = window[kCGWindowLayer as String] as? NSNumber,
              let number = window[kCGWindowNumber as String] as? NSNumber,
              let bounds = window[kCGWindowBounds as String] as? [String: CGFloat],
              let x = bounds["X"], let y = bounds["Y"],
              let width = bounds["Width"], let height = bounds["Height"],
              x.isFinite, y.isFinite, width.isFinite, height.isFinite,
              width > 0, height > 0 else {
            return AXPointWindowMapping(available: false, budgetExceeded: false, matchingWindowIDs: [])
        }
        guard layer.intValue == 0 else { continue }
        let candidate = CGRect(x: x, y: y, width: width, height: height)
        if abs(candidate.minX - geometry.minX) <= 2
            && abs(candidate.minY - geometry.minY) <= 2
            && abs(candidate.width - geometry.width) <= 2
            && abs(candidate.height - geometry.height) <= 2 {
            matches.append(number.uint32Value)
        }
    }
    guard budget.hasTime else {
        return AXPointWindowMapping(available: false, budgetExceeded: true,
                                    matchingWindowIDs: matches)
    }
    return AXPointWindowMapping(available: true, budgetExceeded: false,
                                matchingWindowIDs: matches)
}

func cleanupAXPointQueries(point: CGPoint, budget: AXCleanupBudget)
    -> AXPointQueries<AXUIElement> {
    let systemWide = AXUIElementCreateSystemWide()
    return AXPointQueries<AXUIElement>(
        hitAtPoint: {
            guard budget.hasTime else { return .budgetExceeded }
            var raw: AXUIElement?
            let result = AXUIElementCopyElementAtPosition(systemWide, Float(point.x), Float(point.y), &raw)
            guard budget.hasTime else { return .budgetExceeded }
            guard result == .success else { return .axError(result) }
            guard let raw else { return .malformed }
            return .value(raw)
        },
        processID: { axProcessID($0, budget: budget) },
        role: { axStringValue($0, kAXRoleAttribute as String, budget: budget) },
        containingWindow: { axElementAttribute($0, kAXWindowAttribute as String, budget: budget) },
        parent: { axElementAttribute($0, kAXParentAttribute as String, budget: budget) },
        geometry: { axFrame($0, budget: budget) },
        mapGeometry: { pid, frame in mapAXGeometryToWindow(targetPID: pid, geometry: frame, budget: budget) },
        sameElement: { CFEqual($0, $1) }
    )
}

func cleanupFocusedWindowID(pid: pid_t, expectedWindowID: CGWindowID,
                            budget: AXCleanupBudget) -> (Int?, AXPointResolutionFault?) {
    var hitPID: pid_t?
    var hitRole: String?
    func refusal(_ code: String, _ stage: String, axError: AXError? = nil,
                 geometry: CGRect? = nil, ids: [CGWindowID]? = nil)
        -> (Int?, AXPointResolutionFault?) {
        (nil, AXPointResolutionFault(code: code, stage: stage,
                                     axError: axError.map { Int($0.rawValue) },
                                     hitPID: hitPID, hitRole: hitRole, parentDepth: 0,
                                     matchingWindowIDs: ids, candidateGeometry: geometry))
    }
    guard budget.hasTime else { return refusal("ax_budget_exhausted", "focused_window") }
    let app = AXUIElementCreateApplication(pid)
    var raw: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &raw)
    guard budget.hasTime else { return refusal("ax_budget_exhausted", "focused_window") }
    guard result == .success else { return refusal("ax_focused_window_error", "focused_window", axError: result) }
    guard let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() else {
        return refusal("ax_focused_window_malformed", "focused_window")
    }
    let focused = unsafeBitCast(raw, to: AXUIElement.self)
    switch axProcessID(focused, budget: budget) {
    case .value(let observedPID): hitPID = observedPID
    case .axError(let error): return refusal("ax_focused_window_pid_error", "focused_window_pid", axError: error)
    case .malformed: return refusal("ax_focused_window_pid_malformed", "focused_window_pid")
    case .budgetExceeded: return refusal("ax_budget_exhausted", "focused_window_pid")
    }
    guard hitPID == pid else { return refusal("ax_focused_window_pid_mismatch", "focused_window_pid") }
    switch axStringValue(focused, kAXRoleAttribute as String, budget: budget) {
    case .value(let role): hitRole = safeAXPointRole(role)
    case .axError(let error): return refusal("ax_focused_window_role_error", "focused_window_role", axError: error)
    case .malformed: return refusal("ax_focused_window_role_malformed", "focused_window_role")
    case .budgetExceeded: return refusal("ax_budget_exhausted", "focused_window_role")
    }
    guard hitRole == (kAXWindowRole as String) else {
        return refusal("ax_focused_window_role_mismatch", "focused_window_role")
    }
    let geometry: CGRect
    switch axFrame(focused, budget: budget) {
    case .value(let frame): geometry = frame
    case .axError(let error): return refusal("ax_focused_window_geometry_error", "focused_window_geometry", axError: error)
    case .malformed: return refusal("ax_focused_window_geometry_malformed", "focused_window_geometry")
    case .budgetExceeded: return refusal("ax_budget_exhausted", "focused_window_geometry")
    }
    let mapping = mapAXGeometryToWindow(targetPID: pid, geometry: geometry, budget: budget)
    guard !mapping.budgetExceeded else {
        return refusal("ax_budget_exhausted", "focused_window_mapping", geometry: geometry,
                       ids: mapping.matchingWindowIDs)
    }
    guard mapping.available else {
        return refusal("cg_window_mapping_unavailable", "focused_window_mapping", geometry: geometry)
    }
    guard mapping.uniqueWindowID == expectedWindowID else {
        return refusal("cg_focused_window_id_mismatch", "focused_window_mapping", geometry: geometry,
                       ids: mapping.matchingWindowIDs)
    }
    return (Int(expectedWindowID), nil)
}

func safeAXPointRole(_ role: String) -> String {
    let knownRoles: Set<String> = [
        kAXWindowRole as String, kAXApplicationRole as String, kAXGroupRole as String,
        kAXButtonRole as String, kAXCheckBoxRole as String, kAXImageRole as String,
        "AXLink", kAXProgressIndicatorRole as String, kAXRadioButtonRole as String,
        kAXScrollAreaRole as String, kAXSliderRole as String, kAXStaticTextRole as String,
        kAXTextAreaRole as String, kAXTextFieldRole as String, kAXUnknownRole as String,
    ]
    return knownRoles.contains(role) ? role : "AXOther"
}

func resolveAXPointOwnership<Element>(queries: AXPointQueries<Element>, targetPID: pid_t,
                                      expectedWindowID: CGWindowID, point: CGPoint) throws
    -> AXPointOwnership {
    var hitPID: pid_t?
    var hitRole: String?
    var parentDepth: Int?

    func fault(_ code: String, _ stage: String, axError: AXError? = nil,
               matchingWindowIDs: [CGWindowID]? = nil,
               candidateGeometry: CGRect? = nil) -> AXPointResolutionFault {
        AXPointResolutionFault(code: code, stage: stage,
                               axError: axError.map { Int($0.rawValue) },
                               hitPID: hitPID, hitRole: hitRole,
                               parentDepth: parentDepth,
                               matchingWindowIDs: matchingWindowIDs,
                               candidateGeometry: candidateGeometry)
    }

    func required<Value>(_ value: AXPointRead<Value>, stage: String,
                         code: String) throws -> Value {
        switch value {
        case .value(let result): return result
        case .axError(let error): throw fault(code, stage, axError: error)
        case .malformed: throw fault("ax_value_malformed", stage)
        case .budgetExceeded: throw fault("ax_budget_exhausted", stage)
        }
    }

    guard point.x.isFinite && point.y.isFinite else {
        throw fault("ax_hit_point_invalid", "hit_coordinate")
    }
    guard CGFloat(Float(point.x)) == point.x && CGFloat(Float(point.y)) == point.y else {
        throw fault("ax_hit_point_not_exact", "hit_coordinate")
    }
    let hit = try required(queries.hitAtPoint(), stage: "hit_test", code: "ax_hit_test_error")
    let observedHitPID = try required(queries.processID(hit), stage: "hit_pid", code: "ax_hit_pid_error")
    guard observedHitPID > 0 else {
        throw fault("ax_hit_pid_invalid", "hit_pid")
    }
    hitPID = observedHitPID
    guard observedHitPID == targetPID else {
        throw fault("ax_hit_pid_mismatch", "hit_pid")
    }

    let rawHitRole = try required(queries.role(hit), stage: "hit_role", code: "ax_hit_role_error")
    guard !rawHitRole.isEmpty else { throw fault("ax_hit_role_malformed", "hit_role") }
    hitRole = safeAXPointRole(rawHitRole)

    let candidate: Element
    let resolutionOrigin: String
    var resolvedDepth = 0
    if rawHitRole == (kAXWindowRole as String) {
        candidate = hit
        resolutionOrigin = "hit_is_window"
    } else {
        switch queries.containingWindow(hit) {
        case .value(let window):
            let windowPID = try required(queries.processID(window), stage: "window_attribute_pid",
                                         code: "ax_window_pid_error")
            guard windowPID == targetPID else {
                throw fault("ax_window_pid_mismatch", "window_attribute_pid")
            }
            let rawRole = try required(queries.role(window), stage: "window_attribute_role",
                                       code: "ax_window_role_error")
            guard rawRole == (kAXWindowRole as String) else {
                throw fault("ax_window_role_mismatch", "window_attribute_role")
            }
            candidate = window
            resolutionOrigin = "window_attribute"
        case .axError(let error) where error == .attributeUnsupported || error == .noValue:
            var current = hit
            var visited = [hit]
            var resolved: Element?
            for depth in 1...8 {
                parentDepth = depth
                guard visited.count < 16 else { throw fault("ax_parent_visit_limit", "parent") }
                let parent = try required(queries.parent(current), stage: "parent", code: "ax_parent_error")
                let parentPID = try required(queries.processID(parent), stage: "parent_pid",
                                             code: "ax_parent_pid_error")
                guard parentPID == targetPID else {
                    throw fault("ax_parent_pid_mismatch", "parent_pid")
                }
                guard !visited.contains(where: { queries.sameElement($0, parent) }) else {
                    throw fault("ax_parent_cycle", "parent")
                }
                visited.append(parent)
                let rawRole = try required(queries.role(parent), stage: "parent_role",
                                           code: "ax_parent_role_error")
                if rawRole == (kAXWindowRole as String) {
                    resolved = parent
                    resolvedDepth = depth
                    break
                }
                current = parent
            }
            guard let resolved else {
                throw fault("ax_parent_depth_limit", "parent", candidateGeometry: nil)
            }
            candidate = resolved
            resolutionOrigin = "parent"
        case .axError(let error):
            throw fault("ax_window_attribute_error", "window_attribute", axError: error)
        case .malformed:
            throw fault("ax_window_attribute_malformed", "window_attribute")
        case .budgetExceeded:
            throw fault("ax_budget_exhausted", "window_attribute")
        }
    }

    parentDepth = resolvedDepth
    let candidatePID = try required(queries.processID(candidate), stage: "candidate_pid",
                                    code: "ax_candidate_pid_error")
    guard candidatePID == targetPID else {
        throw fault("ax_candidate_pid_mismatch", "candidate_pid")
    }
    let rawCandidateRole = try required(queries.role(candidate), stage: "candidate_role",
                                        code: "ax_candidate_role_error")
    guard rawCandidateRole == (kAXWindowRole as String) else {
        throw fault("ax_candidate_role_mismatch", "candidate_role")
    }
    let frame = try required(queries.geometry(candidate), stage: "candidate_geometry",
                             code: "ax_candidate_geometry_error")
    guard finiteFrame(frame) else {
        throw fault("ax_candidate_geometry_invalid", "candidate_geometry",
                    candidateGeometry: frame)
    }
    let mapping = queries.mapGeometry(targetPID, frame)
    guard !mapping.budgetExceeded else {
        throw fault("ax_budget_exhausted", "candidate_window_mapping",
                    matchingWindowIDs: mapping.matchingWindowIDs,
                    candidateGeometry: frame)
    }
    guard mapping.available else {
        throw fault("cg_window_mapping_unavailable", "candidate_window_mapping",
                    matchingWindowIDs: mapping.matchingWindowIDs,
                    candidateGeometry: frame)
    }
    guard let candidateWindowID = mapping.uniqueWindowID else {
        throw fault("cg_window_mapping_ambiguous_or_missing", "candidate_window_mapping",
                    matchingWindowIDs: mapping.matchingWindowIDs,
                    candidateGeometry: frame)
    }
    guard candidateWindowID == expectedWindowID else {
        throw fault("cg_window_id_mismatch", "candidate_window_mapping",
                    matchingWindowIDs: mapping.matchingWindowIDs,
                    candidateGeometry: frame)
    }
    return AXPointOwnership(
        hitPID: observedHitPID, hitRole: hitRole ?? "AXUnknown",
        resolutionOrigin: resolutionOrigin, parentDepth: resolvedDepth,
        parentResolutionComplete: true, candidatePID: candidatePID,
        candidateWindowID: candidateWindowID, candidateRole: kAXWindowRole as String,
        candidateGeometry: frame, matchingWindowIDs: mapping.matchingWindowIDs,
        globalPoint: point
    )
}

func finiteJSONNumber(_ value: Double) -> Any {
    value.isFinite ? value : NSNull()
}

func frameJSON(_ frame: CGRect?) -> Any {
    guard let frame else { return NSNull() }
    return [
        "x": finiteJSONNumber(Double(frame.origin.x)),
        "y": finiteJSONNumber(Double(frame.origin.y)),
        "width": finiteJSONNumber(Double(frame.width)),
        "height": finiteJSONNumber(Double(frame.height)),
    ] as [String: Any]
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

func mouseReleaseOwnershipGuardAllows(targetPID: pid_t, expectedWindowID: Int,
                                      accessibilityTrusted: Bool, screenCaptureTrusted: Bool,
                                      sessionUnlocked: Bool, onConsole: Bool, targetProcessLive: Bool,
                                      frontmostPID: pid_t?, focusedWindowID: Int?,
                                      selectedWindowID: Int?, fixtureMatches: Bool,
                                      point: CGPoint, currentWindowFrame: CGRect?,
                                      pointOwner: AXPointOwnership?,
                                      cleanupBudgetAvailable: Bool = true) -> Bool {
    guard let currentWindowFrame else { return false }
    return accessibilityTrusted && screenCaptureTrusted && sessionUnlocked && onConsole
        && targetProcessLive
        && frontmostPID == targetPID
        && focusedWindowID == expectedWindowID
        && selectedWindowID == expectedWindowID
        && fixtureMatches
        && cleanupBudgetAvailable
        && currentWindowFrame.contains(point)
        && pointOwner?.matchesOriginal(
            targetPID: targetPID, expectedWindowID: CGWindowID(expectedWindowID),
            point: point, currentWindowFrame: currentWindowFrame) == true
}

func mouseReleaseOwnershipGuardFailureNames(targetPID: pid_t, expectedWindowID: Int,
                                            accessibilityTrusted: Bool,
                                            screenCaptureTrusted: Bool,
                                            sessionUnlocked: Bool, onConsole: Bool,
                                            targetProcessLive: Bool, frontmostPID: pid_t?,
                                            focusedWindowID: Int?, selectedWindowID: Int?,
                                            fixtureMatches: Bool, point: CGPoint,
                                            currentWindowFrame: CGRect?,
                                            pointOwner: AXPointOwnership?,
                                            cleanupBudgetAvailable: Bool = true) -> [String] {
    var failures: [String] = []
    if !accessibilityTrusted { failures.append("accessibility_trusted") }
    if !screenCaptureTrusted { failures.append("screen_capture_trusted") }
    if !sessionUnlocked { failures.append("session_unlocked") }
    if !onConsole { failures.append("on_console") }
    if !targetProcessLive { failures.append("target_process_live") }
    if frontmostPID != targetPID { failures.append("frontmost_pid_matches_target") }
    if focusedWindowID != expectedWindowID {
        failures.append("focused_window_id_matches_expected")
    }
    if selectedWindowID != expectedWindowID {
        failures.append("selected_window_id_matches_expected")
    }
    if !fixtureMatches { failures.append("fixture_name_matches_selected_window") }
    if !cleanupBudgetAvailable { failures.append("cleanup_ax_budget_available") }
    if currentWindowFrame == nil { failures.append("current_window_frame_available") }
    if currentWindowFrame?.contains(point) != true {
        failures.append("point_inside_current_window_frame")
    }
    if pointOwner?.matchesOriginal(
        targetPID: targetPID, expectedWindowID: CGWindowID(expectedWindowID),
        point: point, currentWindowFrame: currentWindowFrame ?? .zero) != true {
        failures.append("ax_point_owner_matches_original_window")
    }
    return failures
}

struct MouseReleaseGuardSnapshot {
    let targetPID: pid_t
    let expectedWindowID: CGWindowID
    let fixtureName: String
    let accessibilityTrusted: Bool
    let screenCaptureTrusted: Bool
    let sessionLocked: Bool?
    let onConsole: Bool?
    let targetProcessLive: Bool
    let frontmostPID: pid_t?
    let focusedWindowID: Int?
    let selectedWindowID: Int?
    let fixtureMatches: Bool
    let point: CGPoint
    let currentWindowFrame: CGRect?
    let pointOwner: AXPointOwnership?
    let pointOwnerRefusal: AXPointResolutionFault?
    let focusedWindowRefusal: AXPointResolutionFault?
    let cleanupBudgetAvailable: Bool

    var allowsRelease: Bool {
        mouseReleaseOwnershipGuardAllows(
            targetPID: targetPID, expectedWindowID: Int(expectedWindowID),
            accessibilityTrusted: accessibilityTrusted,
            screenCaptureTrusted: screenCaptureTrusted,
            sessionUnlocked: sessionLocked == false, onConsole: onConsole == true,
            targetProcessLive: targetProcessLive, frontmostPID: frontmostPID,
            focusedWindowID: focusedWindowID, selectedWindowID: selectedWindowID,
            fixtureMatches: fixtureMatches, point: point,
            currentWindowFrame: currentWindowFrame, pointOwner: pointOwner,
            cleanupBudgetAvailable: cleanupBudgetAvailable
        )
    }

    var failedPredicates: [String] {
        mouseReleaseOwnershipGuardFailureNames(
            targetPID: targetPID, expectedWindowID: Int(expectedWindowID),
            accessibilityTrusted: accessibilityTrusted,
            screenCaptureTrusted: screenCaptureTrusted,
            sessionUnlocked: sessionLocked == false, onConsole: onConsole == true,
            targetProcessLive: targetProcessLive, frontmostPID: frontmostPID,
            focusedWindowID: focusedWindowID, selectedWindowID: selectedWindowID,
            fixtureMatches: fixtureMatches, point: point,
            currentWindowFrame: currentWindowFrame, pointOwner: pointOwner,
            cleanupBudgetAvailable: cleanupBudgetAvailable
        )
    }

    var successEvidence: [String: Any] {
        [
            "pid": Int(targetPID), "window_id": Int(expectedWindowID), "fixture": fixtureName,
            "selected_window_id": selectedWindowID ?? -1,
            "accessibility_trusted": accessibilityTrusted,
            "screen_capture_trusted": screenCaptureTrusted,
            "session_locked": sessionLocked.map { $0 as Any } ?? NSNull(),
            "on_console": onConsole.map { $0 as Any } ?? NSNull(),
            "target_process_live": targetProcessLive,
            "frontmost_pid": Int(frontmostPID ?? -1),
            "focused_window_id": focusedWindowID ?? -1,
            "point": ["x": Double(point.x), "y": Double(point.y)],
            "current_window_frame": frameJSON(currentWindowFrame),
            "point_owner": pointOwner?.jsonValue as Any? ?? NSNull(),
            "point_owner_refusal": NSNull(),
            "focused_window_refusal": focusedWindowRefusal?.jsonValue as Any? ?? NSNull(),
            "cleanup_ax_budget_available": cleanupBudgetAvailable,
        ]
    }

    var refusalEvidence: [String: Any] {
        let pointOwnerValue: Any = pointOwner?.jsonValue ?? NSNull()
        let refusalValue: Any = pointOwnerRefusal?.jsonValue ?? NSNull()
        return [
            "kind": "guarded_hid_mouse_up_refusal",
            "failed_predicates": failedPredicates,
            "guard_snapshot": [
                "target_pid": Int(targetPID),
                "expected_window_id": Int(expectedWindowID),
                "selected_window_id": selectedWindowID.map { $0 as Any } ?? NSNull(),
                "accessibility_trusted": accessibilityTrusted,
                "screen_capture_trusted": screenCaptureTrusted,
                "session_locked": sessionLocked.map { $0 as Any } ?? NSNull(),
                "on_console": onConsole.map { $0 as Any } ?? NSNull(),
                "target_process_live": targetProcessLive,
                "frontmost_pid": frontmostPID.map { Int($0) as Any } ?? NSNull(),
                "focused_window_id": focusedWindowID.map { $0 as Any } ?? NSNull(),
                "fixture_matches_selected_window": fixtureMatches,
                "point_global": ["x": finiteJSONNumber(Double(point.x)),
                                 "y": finiteJSONNumber(Double(point.y))],
                "current_window_frame": frameJSON(currentWindowFrame),
                "point_owner": pointOwnerValue,
                "point_owner_refusal": refusalValue,
                "focused_window_refusal": focusedWindowRefusal?.jsonValue as Any? ?? NSNull(),
                "cleanup_ax_budget_available": cleanupBudgetAvailable,
            ],
        ]
    }
}

func postGuardedMouseReleaseIfAllowed(_ snapshot: MouseReleaseGuardSnapshot,
                                      post: () -> Void) -> Bool {
    guard snapshot.allowsRelease else { return false }
    post()
    return true
}

func failMouseReleaseGuard(_ snapshot: MouseReleaseGuardSnapshot) -> Never {
    let reason = snapshot.pointOwnerRefusal == nil && snapshot.focusedWindowRefusal == nil
        ? "original fixture focus, current-frame point, or AX point-owner mapping changed"
        : "AX point-owner or focused-window mapping was incomplete or did not identify the original fixture window"
    do {
        let data = try JSONSerialization.data(withJSONObject: snapshot.refusalEvidence,
                                              options: [.sortedKeys, .fragmentsAllowed])
        let encodedEvidence = String(decoding: data, as: UTF8.self)
        fputs("macOS UI helper: guarded HID mouse-up refused: \(reason); refusal_evidence=\(encodedEvidence)\n",
              stderr)
        exit(2)
    } catch {
        fail("guarded HID mouse-up refused: \(reason); refusal evidence could not be encoded")
    }
}

func fixtureAXPointOwnership(targetPID: pid_t = 123, windowID: CGWindowID = 456,
                              point: CGPoint = CGPoint(x: 50, y: 60),
                              frame: CGRect = CGRect(x: 10, y: 20, width: 800, height: 600),
                              hitPID: pid_t? = nil, hitRole: String = "AXWindow",
                              origin: String = "hit_is_window", depth: Int = 0,
                              matchingWindowIDs: [CGWindowID]? = nil) -> AXPointOwnership {
    AXPointOwnership(
        hitPID: hitPID ?? targetPID, hitRole: safeAXPointRole(hitRole),
        resolutionOrigin: origin, parentDepth: depth, parentResolutionComplete: true,
        candidatePID: targetPID, candidateWindowID: windowID,
        candidateRole: kAXWindowRole as String, candidateGeometry: frame,
        matchingWindowIDs: matchingWindowIDs ?? [windowID], globalPoint: point
    )
}

func fixtureMouseReleaseSnapshot(pointOwner: AXPointOwnership? = fixtureAXPointOwnership(),
                                 pointOwnerRefusal: AXPointResolutionFault? = nil,
                                 accessibilityTrusted: Bool = true,
                                 screenCaptureTrusted: Bool = true,
                                 sessionLocked: Bool? = false, onConsole: Bool? = true,
                                 targetProcessLive: Bool = true, frontmostPID: pid_t? = 123,
                                 focusedWindowID: Int? = 456, selectedWindowID: Int? = 456,
                                 fixtureMatches: Bool = true,
                                 point: CGPoint = CGPoint(x: 50, y: 60),
                                 frame: CGRect? = CGRect(x: 10, y: 20, width: 800, height: 600),
                                 focusedWindowRefusal: AXPointResolutionFault? = nil,
                                 cleanupBudgetAvailable: Bool = true)
    -> MouseReleaseGuardSnapshot {
    MouseReleaseGuardSnapshot(
        targetPID: 123, expectedWindowID: 456, fixtureName: "fixture.txt",
        accessibilityTrusted: accessibilityTrusted, screenCaptureTrusted: screenCaptureTrusted,
        sessionLocked: sessionLocked, onConsole: onConsole, targetProcessLive: targetProcessLive,
        frontmostPID: frontmostPID, focusedWindowID: focusedWindowID,
        selectedWindowID: selectedWindowID, fixtureMatches: fixtureMatches,
        point: point, currentWindowFrame: frame, pointOwner: pointOwner,
        pointOwnerRefusal: pointOwnerRefusal, focusedWindowRefusal: focusedWindowRefusal,
        cleanupBudgetAvailable: cleanupBudgetAvailable
    )
}

func validateMouseReleaseOwnershipGuardFixtures() -> Int {
    let wrongPID = fixtureAXPointOwnership(hitPID: 999)
    let wrongWindow = fixtureAXPointOwnership(windowID: 457,
                                               matchingWindowIDs: [457])
    let outsidePoint = fixtureAXPointOwnership(point: CGPoint(x: 900, y: 60))
    let cases: [MouseReleaseGuardSnapshot] = [
        fixtureMouseReleaseSnapshot(),
        fixtureMouseReleaseSnapshot(accessibilityTrusted: false),
        fixtureMouseReleaseSnapshot(screenCaptureTrusted: false),
        fixtureMouseReleaseSnapshot(sessionLocked: true),
        fixtureMouseReleaseSnapshot(onConsole: false),
        fixtureMouseReleaseSnapshot(targetProcessLive: false),
        fixtureMouseReleaseSnapshot(frontmostPID: 999),
        fixtureMouseReleaseSnapshot(focusedWindowID: 455),
        fixtureMouseReleaseSnapshot(focusedWindowID: nil),
        fixtureMouseReleaseSnapshot(selectedWindowID: 455),
        fixtureMouseReleaseSnapshot(fixtureMatches: false),
        fixtureMouseReleaseSnapshot(point: CGPoint(x: 900, y: 60)),
        fixtureMouseReleaseSnapshot(frame: nil),
        fixtureMouseReleaseSnapshot(frame: CGRect(x: 10, y: 20, width: 30, height: 30)),
        fixtureMouseReleaseSnapshot(frame: CGRect(x: 100, y: 100, width: 800, height: 600)),
        fixtureMouseReleaseSnapshot(pointOwner: wrongPID),
        fixtureMouseReleaseSnapshot(pointOwner: wrongWindow),
        fixtureMouseReleaseSnapshot(pointOwner: outsidePoint),
    ]
    guard cases[0].allowsRelease,
          cases.dropFirst().allSatisfy({ !$0.allowsRelease }) else {
        fail("guarded mouse-release fixtures did not reject lost ownership, focus, point, or session")
    }
    var posts = 0
    for snapshot in cases.dropFirst() {
        guard !postGuardedMouseReleaseIfAllowed(snapshot, post: { posts += 1 }) else {
            fail("guarded HID cleanup posted after an invalid owner or guard snapshot")
        }
    }
    guard posts == 0,
          postGuardedMouseReleaseIfAllowed(cases[0], post: { posts += 1 }), posts == 1 else {
        fail("guarded HID cleanup post seam did not preserve the valid owner case")
    }
    return cases.count
}

func validateMouseReleaseRefusalFixtures() -> Int {
    let valid = fixtureAXPointOwnership()
    let wrongPID = fixtureAXPointOwnership(hitPID: 999)
    let wrongWindow = fixtureAXPointOwnership(windowID: 457, matchingWindowIDs: [457])
    let cases: [(String, MouseReleaseGuardSnapshot)] = [
        ("accessibility_trusted", fixtureMouseReleaseSnapshot(accessibilityTrusted: false)),
        ("screen_capture_trusted", fixtureMouseReleaseSnapshot(screenCaptureTrusted: false)),
        ("session_unlocked", fixtureMouseReleaseSnapshot(sessionLocked: true)),
        ("on_console", fixtureMouseReleaseSnapshot(onConsole: false)),
        ("target_process_live", fixtureMouseReleaseSnapshot(targetProcessLive: false)),
        ("frontmost_pid_matches_target", fixtureMouseReleaseSnapshot(frontmostPID: 999)),
        ("frontmost_pid_matches_target", fixtureMouseReleaseSnapshot(frontmostPID: nil)),
        ("focused_window_id_matches_expected", fixtureMouseReleaseSnapshot(focusedWindowID: 455)),
        ("focused_window_id_matches_expected", fixtureMouseReleaseSnapshot(focusedWindowID: nil)),
        ("selected_window_id_matches_expected", fixtureMouseReleaseSnapshot(selectedWindowID: 455)),
        ("selected_window_id_matches_expected", fixtureMouseReleaseSnapshot(selectedWindowID: nil)),
        ("fixture_name_matches_selected_window", fixtureMouseReleaseSnapshot(fixtureMatches: false)),
        ("current_window_frame_available", fixtureMouseReleaseSnapshot(frame: nil)),
        ("point_inside_current_window_frame",
         fixtureMouseReleaseSnapshot(point: CGPoint(x: 900, y: 60))),
        ("point_inside_current_window_frame",
         fixtureMouseReleaseSnapshot(frame: CGRect(x: 100, y: 100, width: 800, height: 600))),
        ("ax_point_owner_matches_original_window", fixtureMouseReleaseSnapshot(pointOwner: wrongPID)),
        ("ax_point_owner_matches_original_window", fixtureMouseReleaseSnapshot(pointOwner: wrongWindow)),
        ("ax_point_owner_matches_original_window",
         fixtureMouseReleaseSnapshot(pointOwner: nil,
                                     pointOwnerRefusal: AXPointResolutionFault(
                                        code: "ax_hit_pid_mismatch", stage: "hit_pid",
                                        axError: nil, hitPID: 999, hitRole: nil,
                                        parentDepth: nil, matchingWindowIDs: nil,
                                        candidateGeometry: nil))),
    ]
    guard valid.matchesOriginal(targetPID: 123, expectedWindowID: 456,
                                point: CGPoint(x: 50, y: 60),
                                currentWindowFrame: CGRect(x: 10, y: 20, width: 800, height: 600)) else {
        fail("guarded mouse-release refusal fixtures have no valid owner snapshot")
    }
    for (expected, snapshot) in cases {
        guard snapshot.failedPredicates.contains(expected), !snapshot.failedPredicates.isEmpty else {
            fail("guarded mouse-release refusal name did not match rejected predicate \(expected)")
        }
        var posts = 0
        guard !postGuardedMouseReleaseIfAllowed(snapshot, post: { posts += 1 }), posts == 0 else {
            fail("guarded mouse-release refusal fixture enqueued after \(expected)")
        }
    }
    let snapshot = fixtureMouseReleaseSnapshot(
        pointOwner: nil,
        pointOwnerRefusal: AXPointResolutionFault(
            code: "ax_hit_pid_mismatch", stage: "hit_pid", axError: nil,
            hitPID: 999, hitRole: nil, parentDepth: nil,
            matchingWindowIDs: nil, candidateGeometry: nil))
    guard let data = try? JSONSerialization.data(withJSONObject: snapshot.refusalEvidence,
                                                 options: [.sortedKeys, .fragmentsAllowed]),
          let decoded = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          decoded["kind"] as? String == "guarded_hid_mouse_up_refusal",
          let guardSnapshot = decoded["guard_snapshot"] as? [String: Any],
          Set(guardSnapshot.keys) == Set([
              "target_pid", "expected_window_id", "selected_window_id",
              "accessibility_trusted", "screen_capture_trusted", "session_locked",
              "on_console", "target_process_live", "frontmost_pid", "focused_window_id",
              "fixture_matches_selected_window", "point_global", "current_window_frame",
              "point_owner", "point_owner_refusal", "focused_window_refusal",
              "cleanup_ax_budget_available",
          ]),
          guardSnapshot["point_owner"] is NSNull,
          let refusal = guardSnapshot["point_owner_refusal"] as? [String: Any],
          refusal["code"] as? String == "ax_hit_pid_mismatch",
          (refusal["hit_pid"] as? Int) == 999,
          refusal["hit_role"] == nil else {
        fail("guarded mouse-release refusal evidence was not safe structured JSON")
    }
    return cases.count
}

struct AXPointFixtureNode {
    let processID: AXPointRead<pid_t>
    let role: AXPointRead<String>
    let containingWindow: AXPointRead<Int>
    let parent: AXPointRead<Int>
    let geometry: AXPointRead<CGRect>
}

final class AXPointFixtureReadCounts {
    var hit = 0
    var role = 0
    var window = 0
    var parent = 0
}

func axPointFixtureQueries(nodes: [Int: AXPointFixtureNode], hit: AXPointRead<Int>,
                           mapping: AXPointWindowMapping,
                           reads: AXPointFixtureReadCounts) -> AXPointQueries<Int> {
    AXPointQueries<Int>(
        hitAtPoint: { reads.hit += 1; return hit },
        processID: { nodes[$0]?.processID ?? .malformed },
        role: { reads.role += 1; return nodes[$0]?.role ?? .malformed },
        containingWindow: { reads.window += 1; return nodes[$0]?.containingWindow ?? .malformed },
        parent: { reads.parent += 1; return nodes[$0]?.parent ?? .malformed },
        geometry: { nodes[$0]?.geometry ?? .malformed },
        mapGeometry: { _, _ in mapping },
        sameElement: { $0 == $1 }
    )
}

func expectAXPointRefusal<Element>(queries: AXPointQueries<Element>, expectedCode: String,
                                   reads: AXPointFixtureReadCounts,
                                   point: CGPoint = CGPoint(x: 50, y: 60)) -> AXPointResolutionFault {
    do {
        _ = try resolveAXPointOwnership(queries: queries, targetPID: 123,
                                        expectedWindowID: 456,
                                        point: point)
        fail("AX point fixture unexpectedly resolved owner; expected \(expectedCode)")
    } catch let fault as AXPointResolutionFault {
        guard fault.code == expectedCode else {
            fail("AX point fixture returned \(fault.code), expected \(expectedCode)")
        }
        let snapshot = fixtureMouseReleaseSnapshot(pointOwner: nil, pointOwnerRefusal: fault,
                                                   point: point)
        var posts = 0
        guard !postGuardedMouseReleaseIfAllowed(snapshot, post: { posts += 1 }), posts == 0 else {
            fail("AX point refusal \(expectedCode) reached the guarded HID post seam")
        }
        return fault
    } catch {
        fail("AX point fixture raised an unexpected error: \(error)")
    }
}

func validateAXPointOwnershipFixtures() -> Int {
    let frame = CGRect(x: 10, y: 20, width: 800, height: 600)
    let point = CGPoint(x: 50, y: 60)
    let uniqueMap = AXPointWindowMapping(available: true, budgetExceeded: false,
                                         matchingWindowIDs: [456])
    func node(pid: AXPointRead<pid_t> = .value(123),
              role: AXPointRead<String> = .value(kAXWindowRole as String),
              window: AXPointRead<Int> = .axError(.attributeUnsupported),
              parent: AXPointRead<Int> = .malformed,
              geometry: AXPointRead<CGRect> = .value(frame)) -> AXPointFixtureNode {
        AXPointFixtureNode(processID: pid, role: role, containingWindow: window,
                           parent: parent, geometry: geometry)
    }
    func queries(_ nodes: [Int: AXPointFixtureNode], hit: AXPointRead<Int> = .value(1),
                 map: AXPointWindowMapping = uniqueMap,
                 reads: AXPointFixtureReadCounts = AXPointFixtureReadCounts())
        -> (AXPointQueries<Int>, AXPointFixtureReadCounts) {
        (axPointFixtureQueries(nodes: nodes, hit: hit, mapping: map, reads: reads), reads)
    }
    var count = 0

    let (directQuery, _) = queries([1: node()])
    let direct = try! resolveAXPointOwnership(queries: directQuery, targetPID: 123,
                                               expectedWindowID: 456, point: point)
    guard direct.resolutionOrigin == "hit_is_window", direct.parentDepth == 0,
          direct.parentResolutionComplete, direct.hitPID == 123,
          direct.candidateWindowID == 456 else {
        fail("direct AXWindow point-owner fixture did not map the exact original window")
    }
    let directSnapshot = fixtureMouseReleaseSnapshot(pointOwner: direct)
    var directPosts = 0
    guard postGuardedMouseReleaseIfAllowed(directSnapshot, post: { directPosts += 1 }),
          directPosts == 1 else {
        fail("valid direct AXWindow owner did not pass the guarded post seam")
    }
    count += 1

    let quarterPoint = CGPoint(x: 1234.75, y: 702.75)
    let quarterFrame = CGRect(x: 1200, y: 650, width: 100, height: 100)
    let (quarterQuery, _) = queries([
        1: node(geometry: .value(quarterFrame)),
    ])
    let quarterOwner = try! resolveAXPointOwnership(
        queries: quarterQuery, targetPID: 123, expectedWindowID: 456, point: quarterPoint)
    let quarterSnapshot = fixtureMouseReleaseSnapshot(
        pointOwner: quarterOwner, point: quarterPoint, frame: quarterFrame)
    var quarterPosts = 0
    guard postGuardedMouseReleaseIfAllowed(quarterSnapshot, post: { quarterPosts += 1 }),
          quarterPosts == 1 else {
        fail("exact quarter-point AX coordinate failed to reach the original-window post seam")
    }
    count += 1

    let inexactCoordinates: [(CGPoint, String)] = [
        (CGPoint(x: 1234.75001, y: 702.75), "ax_hit_point_not_exact"),
        (CGPoint(x: CGFloat.greatestFiniteMagnitude, y: 702.75), "ax_hit_point_not_exact"),
        (CGPoint(x: CGFloat.infinity, y: 702.75), "ax_hit_point_invalid"),
        (CGPoint(x: CGFloat.nan, y: 702.75), "ax_hit_point_invalid"),
    ]
    for (invalidPoint, code) in inexactCoordinates {
        let coordinateReads = AXPointFixtureReadCounts()
        let (coordinateQuery, _) = queries([1: node()], reads: coordinateReads)
        _ = expectAXPointRefusal(queries: coordinateQuery, expectedCode: code,
                                 reads: coordinateReads, point: invalidPoint)
        guard coordinateReads.hit == 0, coordinateReads.role == 0,
              coordinateReads.window == 0, coordinateReads.parent == 0 else {
            fail("invalid AX Float point performed accessibility reads before refusing")
        }
        count += 1
    }

    let expiredBudget = AXCleanupBudget(until: 0, now: { 1 })
    var expiredAttributeCalls = 0
    let expiredAttribute = copyAXAttributeValue(budget: expiredBudget) { _ in
        expiredAttributeCalls += 1
        return .success
    }
    guard case .budgetExceeded = expiredAttribute, expiredAttributeCalls == 0 else {
        fail("expired AX cleanup budget called the native attribute reader")
    }
    count += 1

    let (attributeQuery, _) = queries([
        1: node(role: .value(kAXButtonRole as String), window: .value(2)),
        2: node(),
    ])
    let attributeOwner = try! resolveAXPointOwnership(queries: attributeQuery, targetPID: 123,
                                                       expectedWindowID: 456, point: point)
    guard attributeOwner.resolutionOrigin == "window_attribute",
          attributeOwner.parentDepth == 0 else {
        fail("AXWindow attribute fixture did not map the owning window")
    }
    let attributeSnapshot = fixtureMouseReleaseSnapshot(pointOwner: attributeOwner)
    var attributePosts = 0
    guard postGuardedMouseReleaseIfAllowed(attributeSnapshot, post: { attributePosts += 1 }),
          attributePosts == 1 else {
        fail("valid containing-window attribute did not pass the guarded post seam")
    }
    count += 1

    let (parentQuery, _) = queries([
        1: node(role: .value(kAXGroupRole as String), parent: .value(2)),
        2: node(),
    ])
    let parentOwner = try! resolveAXPointOwnership(queries: parentQuery, targetPID: 123,
                                                    expectedWindowID: 456, point: point)
    guard parentOwner.resolutionOrigin == "parent", parentOwner.parentDepth == 1 else {
        fail("bounded parent fixture did not identify the original AXWindow")
    }
    let parentSnapshot = fixtureMouseReleaseSnapshot(pointOwner: parentOwner)
    var parentPosts = 0
    guard postGuardedMouseReleaseIfAllowed(parentSnapshot, post: { parentPosts += 1 }),
          parentPosts == 1 else {
        fail("valid bounded parent mapping did not pass the guarded post seam")
    }
    count += 1

    let foreignReads = AXPointFixtureReadCounts()
    let (foreignQuery, _) = queries([1: node(pid: .value(999))], reads: foreignReads)
    let foreign = expectAXPointRefusal(queries: foreignQuery, expectedCode: "ax_hit_pid_mismatch",
                                       reads: foreignReads)
    guard foreign.hitPID == 999, foreign.hitRole == nil, foreignReads.role == 0,
          foreignReads.window == 0, foreignReads.parent == 0 else {
        fail("foreign PID was not rejected before role/window/parent reads")
    }
    count += 1

    let (unknownPIDQuery, unknownReads) = queries([
        1: node(pid: .axError(.cannotComplete)),
    ])
    _ = expectAXPointRefusal(queries: unknownPIDQuery, expectedCode: "ax_hit_pid_error",
                              reads: unknownReads)
    count += 1

    let (missingHitQuery, missingHitReads) = queries([:], hit: .malformed)
    _ = expectAXPointRefusal(queries: missingHitQuery, expectedCode: "ax_value_malformed",
                              reads: missingHitReads)
    count += 1

    let (roleTypeQuery, roleTypeReads) = queries([
        1: node(role: .malformed),
    ])
    _ = expectAXPointRefusal(queries: roleTypeQuery, expectedCode: "ax_value_malformed",
                              reads: roleTypeReads)
    count += 1

    let (budgetQuery, budgetReads) = queries([:], hit: .budgetExceeded)
    _ = expectAXPointRefusal(queries: budgetQuery, expectedCode: "ax_budget_exhausted",
                              reads: budgetReads)
    count += 1

    let (partialQuery, partialReads) = queries([
        1: node(role: .value(kAXGroupRole as String), parent: .axError(.cannotComplete)),
    ])
    let partial = expectAXPointRefusal(queries: partialQuery, expectedCode: "ax_parent_error",
                                       reads: partialReads)
    guard partial.axError == Int(AXError.cannotComplete.rawValue) else {
        fail("partial parent traversal did not retain its AX error")
    }
    count += 1

    let foreignParentReads = AXPointFixtureReadCounts()
    let (foreignParentQuery, _) = queries([
        1: node(role: .value(kAXGroupRole as String), parent: .value(2)),
        2: node(pid: .value(999)),
    ], reads: foreignParentReads)
    _ = expectAXPointRefusal(queries: foreignParentQuery,
                             expectedCode: "ax_parent_pid_mismatch",
                             reads: foreignParentReads)
    guard foreignParentReads.role == 1 else {
        fail("foreign parent role was read before its PID was rejected")
    }
    count += 1

    let (malformedWindowQuery, malformedWindowReads) = queries([
        1: node(role: .value(kAXButtonRole as String), window: .malformed),
    ])
    _ = expectAXPointRefusal(queries: malformedWindowQuery,
                             expectedCode: "ax_window_attribute_malformed",
                             reads: malformedWindowReads)
    count += 1

    let cycleReads = AXPointFixtureReadCounts()
    let (cycleQuery, _) = queries([
        1: node(role: .value(kAXGroupRole as String), parent: .value(1)),
    ], reads: cycleReads)
    _ = expectAXPointRefusal(queries: cycleQuery, expectedCode: "ax_parent_cycle",
                              reads: cycleReads)
    count += 1

    var depthNodes = [Int: AXPointFixtureNode]()
    for identity in 1...9 {
        depthNodes[identity] = node(role: .value(kAXGroupRole as String),
                                    parent: identity < 9 ? .value(identity + 1) : .malformed)
    }
    let (depthQuery, depthReads) = queries(depthNodes)
    let depth = expectAXPointRefusal(queries: depthQuery, expectedCode: "ax_parent_depth_limit",
                                     reads: depthReads)
    guard depth.parentDepth == 8 else { fail("parent-depth limit fixture did not stop at depth 8") }
    count += 1

    let foreignWindowReads = AXPointFixtureReadCounts()
    let (foreignWindowQuery, _) = queries([
        1: node(role: .value(kAXButtonRole as String), window: .value(2)),
        2: node(pid: .value(999)),
    ], reads: foreignWindowReads)
    _ = expectAXPointRefusal(queries: foreignWindowQuery,
                             expectedCode: "ax_window_pid_mismatch",
                             reads: foreignWindowReads)
    guard foreignWindowReads.role == 1 else {
        fail("foreign containing-window element role was read before PID validation")
    }
    count += 1

    let wrongRoleReads = AXPointFixtureReadCounts()
    let (wrongRoleQuery, _) = queries([
        1: node(role: .value(kAXButtonRole as String), window: .value(2)),
        2: node(role: .value(kAXButtonRole as String)),
    ], reads: wrongRoleReads)
    _ = expectAXPointRefusal(queries: wrongRoleQuery, expectedCode: "ax_window_role_mismatch",
                              reads: wrongRoleReads)
    count += 1

    let (nonfiniteQuery, nonfiniteReads) = queries([
        1: node(geometry: .value(CGRect(x: CGFloat.infinity, y: 20, width: 800, height: 600))),
    ])
    _ = expectAXPointRefusal(queries: nonfiniteQuery, expectedCode: "ax_candidate_geometry_invalid",
                              reads: nonfiniteReads)
    count += 1

    let (unavailableMapQuery, unavailableMapReads) = queries(
        [1: node()], map: AXPointWindowMapping(available: false, budgetExceeded: false,
                                                matchingWindowIDs: []))
    _ = expectAXPointRefusal(queries: unavailableMapQuery,
                              expectedCode: "cg_window_mapping_unavailable",
                              reads: unavailableMapReads)
    count += 1

    let (wrongIDQuery, wrongIDReads) = queries(
        [1: node()], map: AXPointWindowMapping(available: true, budgetExceeded: false,
                                               matchingWindowIDs: [457]))
    _ = expectAXPointRefusal(queries: wrongIDQuery, expectedCode: "cg_window_id_mismatch",
                              reads: wrongIDReads)
    count += 1

    let (ambiguousQuery, ambiguousReads) = queries(
        [1: node()], map: AXPointWindowMapping(available: true, budgetExceeded: false,
                                               matchingWindowIDs: [456, 457]))
    _ = expectAXPointRefusal(queries: ambiguousQuery,
                              expectedCode: "cg_window_mapping_ambiguous_or_missing",
                              reads: ambiguousReads)
    count += 1

    let (mappingBudgetQuery, mappingBudgetReads) = queries(
        [1: node()], map: AXPointWindowMapping(available: false, budgetExceeded: true,
                                               matchingWindowIDs: []))
    _ = expectAXPointRefusal(queries: mappingBudgetQuery,
                             expectedCode: "ax_budget_exhausted",
                             reads: mappingBudgetReads)
    count += 1

    var guardLossPosts = 0
    let guardLosses = [
        fixtureMouseReleaseSnapshot(pointOwner: direct, accessibilityTrusted: false),
        fixtureMouseReleaseSnapshot(pointOwner: direct, screenCaptureTrusted: false),
        fixtureMouseReleaseSnapshot(pointOwner: direct, sessionLocked: true),
        fixtureMouseReleaseSnapshot(pointOwner: direct, onConsole: false),
        fixtureMouseReleaseSnapshot(pointOwner: direct, targetProcessLive: false),
        fixtureMouseReleaseSnapshot(pointOwner: direct, frontmostPID: 999),
        fixtureMouseReleaseSnapshot(pointOwner: direct, focusedWindowID: 455),
        fixtureMouseReleaseSnapshot(pointOwner: direct, selectedWindowID: 455),
        fixtureMouseReleaseSnapshot(pointOwner: direct, fixtureMatches: false),
        fixtureMouseReleaseSnapshot(pointOwner: direct, point: CGPoint(x: 900, y: 60)),
        fixtureMouseReleaseSnapshot(pointOwner: direct, focusedWindowID: nil,
                                    focusedWindowRefusal: AXPointResolutionFault(
                                        code: "ax_focused_window_pid_mismatch",
                                        stage: "focused_window_pid", axError: nil,
                                        hitPID: 999, hitRole: nil, parentDepth: 0,
                                        matchingWindowIDs: nil, candidateGeometry: nil)),
        fixtureMouseReleaseSnapshot(pointOwner: direct, cleanupBudgetAvailable: false),
    ]
    for snapshot in guardLosses {
        guard !postGuardedMouseReleaseIfAllowed(snapshot, post: { guardLossPosts += 1 }) else {
            fail("AX point-owner guard-loss fixture enqueued a mouse-up")
        }
    }
    guard guardLossPosts == 0 else { fail("a lost original guard reached the AX cleanup post seam") }
    count += guardLosses.count

    return count
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

func settleInputSenderAfterPost() -> (requestedMilliseconds: Int, elapsedMilliseconds: Int) {
    let startedAt = ProcessInfo.processInfo.systemUptime
    let requiredSeconds = Double(inputSenderSettleRequestedMS) / 1000
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
    guard elapsedMilliseconds >= inputSenderSettleMinimumMS else {
        fail("synthetic input sender RunLoop settled for only \(elapsedMilliseconds)ms")
    }
    return (inputSenderSettleRequestedMS, elapsedMilliseconds)
}

func validateInputSenderSettleFixture() -> (requestedMilliseconds: Int, elapsedMilliseconds: Int) {
    let settle = settleInputSenderAfterPost()
    guard settle.elapsedMilliseconds >= inputSenderSettleMinimumMS else {
        fail("synthetic input sender RunLoop fixture observed only \(settle.elapsedMilliseconds)ms")
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

func guardedMouseReleaseTarget(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                               x: Double, y: Double, budget: AXCleanupBudget)
    -> MouseReleaseGuardSnapshot {
    let point = CGPoint(x: x, y: y)
    let rawWindows = windowRecords()
    let fixtureWindows = rawWindows.filter { record in
        let title = record["title"] as? String ?? ""
        let width = record["width"] as? Double ?? 0
        let height = record["height"] as? Double ?? 0
        return (recordPID(record) == pid)
            && title.localizedCaseInsensitiveContains(fixtureName)
            && width >= 700 && height >= 500
    }
    let selected = rawWindows.first(where: {
        recordPID($0) == pid && ($0["window_id"] as? Int) == Int(windowID)
    })
    let selectedWindowID = selected?["window_id"] as? Int
    let frame: CGRect? = selected.flatMap { record in
        guard let x = record["x"] as? Double, let y = record["y"] as? Double,
              let width = record["width"] as? Double, let height = record["height"] as? Double else {
            return nil
        }
        let candidate = CGRect(x: x, y: y, width: width, height: height)
        return finiteFrame(candidate) ? candidate : nil
    }
    let focused = cleanupFocusedWindowID(pid: pid, expectedWindowID: windowID, budget: budget)
    let queries = cleanupAXPointQueries(point: point, budget: budget)
    let owner: AXPointOwnership?
    let ownerRefusal: AXPointResolutionFault?
    do {
        owner = try resolveAXPointOwnership(queries: queries, targetPID: pid,
                                            expectedWindowID: windowID, point: point)
        ownerRefusal = nil
    } catch let refusal as AXPointResolutionFault {
        owner = nil
        ownerRefusal = refusal
    } catch {
        owner = nil
        ownerRefusal = AXPointResolutionFault(
            code: "ax_point_owner_unexpected_error", stage: "resolve",
            axError: nil, hitPID: nil, hitRole: nil, parentDepth: nil,
            matchingWindowIDs: nil, candidateGeometry: nil)
    }
    let frontmostPID = NSWorkspace.shared.frontmostApplication?.processIdentifier
    let session = consoleSessionFlags()
    let accessibilityTrusted = AXIsProcessTrusted()
    let screenCaptureTrusted = CGPreflightScreenCaptureAccess()
    let sessionLocked = session["session_locked"] as? Bool
    let onConsole = session["on_console"] as? Bool
    let fixtureMatches = fixtureWindows.count == 1
        && fixtureWindows.first?["window_id"] as? Int == Int(windowID)
    let targetProcessLive = NSRunningApplication(processIdentifier: pid) != nil
    return MouseReleaseGuardSnapshot(
        targetPID: pid, expectedWindowID: windowID, fixtureName: fixtureName,
        accessibilityTrusted: accessibilityTrusted,
        screenCaptureTrusted: screenCaptureTrusted,
        sessionLocked: sessionLocked, onConsole: onConsole,
        targetProcessLive: targetProcessLive, frontmostPID: frontmostPID,
        focusedWindowID: focused.0, selectedWindowID: selectedWindowID,
        fixtureMatches: fixtureMatches, point: point, currentWindowFrame: frame,
        pointOwner: owner, pointOwnerRefusal: ownerRefusal,
        focusedWindowRefusal: focused.1, cleanupBudgetAvailable: budget.hasTime
    )
}

func recordPID(_ record: [String: Any]) -> pid_t? {
    guard let pid = record["pid"] as? Int else { return nil }
    return pid_t(pid)
}

func postCleanupMouseRelease(pid: pid_t, windowID: CGWindowID, fixtureName: String,
                             x: Double, y: Double) {
    let budget = AXCleanupBudget()
    let initialSnapshot = guardedMouseReleaseTarget(pid: pid, windowID: windowID,
                                                    fixtureName: fixtureName, x: x, y: y,
                                                    budget: budget)
    guard initialSnapshot.allowsRelease else { failMouseReleaseGuard(initialSnapshot) }
    guard let source = CGEventSource(stateID: .hidSystemState) else {
        fail("cannot create guarded HID mouse-up cleanup event source")
    }
    guard let event = CGEvent(
        mouseEventSource: source,
        mouseType: .leftMouseUp,
        mouseCursorPosition: CGPoint(x: x, y: y),
        mouseButton: .left
    ) else {
        fail("cannot create guarded HID mouse-up cleanup event")
    }
    resetUnmodifiedInputFlags(event)
    guard event.flags.rawValue == 0 else { fail("guarded HID mouse-up cleanup retained modifier flags") }
    let flagsBeforePost = event.flags.rawValue
    let postSnapshot = guardedMouseReleaseTarget(pid: pid, windowID: windowID,
                                                fixtureName: fixtureName, x: x, y: y,
                                                budget: budget)
    var enqueued = false
    guard postGuardedMouseReleaseIfAllowed(postSnapshot, post: {
        guard budget.hasTime else { return }
        event.post(tap: .cghidEventTap)
        enqueued = true
    }), enqueued else {
        failMouseReleaseGuard(postSnapshot)
    }
    // This keeps the original HID event-tap route and posts only while the fresh
    // AX point owner, focused window, and all release guards still map to the
    // selected fixture. Enqueue does not prove delivery or receipt.
    let senderSettle = withExtendedLifetime((source, event)) { settleInputSenderAfterPost() }
    emit(["pid": Int(pid), "window_id": Int(windowID), "fixture": fixtureName,
          "cleanup": "guarded-hid-mouse-up", "delivery_route": "guarded_cghid_event_tap",
          "guarded_hid_enqueue": true, "guard_evidence": postSnapshot.successEvidence,
          "event_flags_before_post_raw": flagsBeforePost,
          "event_flags_raw": flagsBeforePost,
          "event_flags_after_post_raw": event.flags.rawValue,
          "sender_runloop_settle_requested_ms": senderSettle.requestedMilliseconds,
          "sender_runloop_settle_ms": senderSettle.elapsedMilliseconds])
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
    let senderSettle = settleInputSenderAfterPost()
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
    let senderSettle = settleInputSenderAfterPost()
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
    guard let source = CGEventSource(stateID: .hidSystemState) else {
        fail("cannot create mouse event source")
    }
    guard let event = CGEvent(
        mouseEventSource: source,
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
    let senderSettle = withExtendedLifetime(source) { settleInputSenderAfterPost() }
    emit(["pid": Int(pid), "window_id": Int(windowID), "event": action, "posted": true,
          "event_flags_before_post_raw": flagsBeforePost,
          "event_flags_raw": flagsBeforePost,
          "event_flags_after_post_raw": event.flags.rawValue,
          "sender_runloop_settle_requested_ms": senderSettle.requestedMilliseconds,
          "sender_runloop_settle_ms": senderSettle.elapsedMilliseconds])
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
    let senderSettle = settleInputSenderAfterPost()
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
    let senderSettle = settleInputSenderAfterPost()
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
    fail("usage: macos_ui.swift permissions|guard-fixtures|windows PID|displays PID WINDOW_ID FIXTURE|ax-state PID WINDOW_ID FIXTURE|ax-press PID WINDOW_ID FIXTURE FOCUSED_WINDOW_ID BUTTON_TITLE|activate PID WINDOW_ID FIXTURE|capture PID WINDOW_ID FIXTURE PNG|mouse-event PID WINDOW_ID FIXTURE ACTION X Y|key-event PID WINDOW_ID FIXTURE ACTION CODE FLAGS|text-event PID WINDOW_ID FIXTURE ACTION TEXT|cleanup-mouse PID WINDOW_ID FIXTURE X Y|cleanup-key PID CODE FLAGS|cleanup-text PID TEXT|resize PID WINDOW_ID FIXTURE WIDTH HEIGHT|ocr PNG|colors PNG RED GREEN BLUE")
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
    let mouseReleaseOwnershipFixtures = validateMouseReleaseOwnershipGuardFixtures()
    let mouseReleaseRefusalFixtures = validateMouseReleaseRefusalFixtures()
    let axPointOwnershipFixtures = validateAXPointOwnershipFixtures()
    let inputSenderSettle = validateInputSenderSettleFixture()
    emit(["session_fixtures": "passed", "frontmost_guard_fixtures": "passed",
          "focused_window_guard_fixtures": "passed", "ax_press_guard_fixtures": "passed",
          "ax_traversal_fixtures": "passed", "plain_input_flags_fixtures": "passed",
          "mouse_release_ownership_fixtures": "passed",
          "mouse_release_ownership_fixture_count": mouseReleaseOwnershipFixtures,
          "mouse_release_refusal_fixtures": "passed",
          "mouse_release_refusal_fixture_count": mouseReleaseRefusalFixtures,
          "ax_point_ownership_fixtures": "passed",
          "ax_point_ownership_fixture_count": axPointOwnershipFixtures,
          "input_sender_settle_fixtures": "passed",
          "input_sender_settle_requested_ms": inputSenderSettle.requestedMilliseconds,
          "input_sender_settle_ms": inputSenderSettle.elapsedMilliseconds])
case "windows":
    guard arguments.count == 2 else { fail("windows requires PID") }
    let pid = integer(arguments[1], "PID")
    let frontmost = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1
    emit(["frontmost_pid": Int(frontmost), "windows": windowRecords().filter { ($0["pid"] as? Int) == pid }])
case "displays":
    guard arguments.count == 4 else { fail("displays requires PID WINDOW_ID FIXTURE") }
    let pid = pid_t(integer(arguments[1], "PID"))
    let windowID = CGWindowID(integer(arguments[2], "window ID"))
    emit(["displays": displayRecords(pid: pid, windowID: windowID, fixtureName: arguments[3])])
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
    guard arguments.count == 6 else { fail("cleanup-mouse requires PID WINDOW_ID FIXTURE X Y") }
    let windowID = integer(arguments[2], "window ID")
    guard (0...Int(UInt32.max)).contains(windowID) else { fail("window ID is out of range") }
    postCleanupMouseRelease(pid: pid_t(integer(arguments[1], "PID")),
                            windowID: CGWindowID(windowID), fixtureName: arguments[3],
                            x: number(arguments[4], "X"), y: number(arguments[5], "Y"))
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
