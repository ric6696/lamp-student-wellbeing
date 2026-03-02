import Foundation

final class WatchCompanionController {
    static let shared = WatchCompanionController()

    var onStatusUpdate: ((String) -> Void)? {
        didSet {
            bridge.onStatusUpdate = onStatusUpdate
        }
    }

    private let bridge = WatchPhoneBridge.shared

    private init() {
        bridge.onStatusUpdate = onStatusUpdate
    }

    func start() {
        bridge.onStatusUpdate = onStatusUpdate
        bridge.start()
    }
}
