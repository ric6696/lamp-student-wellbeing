import Foundation
import CoreMotion

final class MotionManager {
    static let shared = MotionManager()
    private let activityManager = CMMotionActivityManager()
    private var _currentContext: MotionContext = .unknown
    private let lock = DispatchQueue(label: "motion.context.lock")

    var currentContext: MotionContext {
        lock.sync { _currentContext }
    }

    func start() {
        guard CMMotionActivityManager.isActivityAvailable() else { return }
        activityManager.startActivityUpdates(to: .main) { [weak self] activity in
            guard let self = self, let act = activity else { return }
            let newContext = MotionContext(from: act)
            self.lock.sync { self._currentContext = newContext }
        }
    }
}

private extension MotionContext {
    init(from activity: CMMotionActivity) {
        if activity.automotive { self = .driving }
        else if activity.cycling { self = .cycling }
        else if activity.running { self = .running }
        else if activity.walking { self = .walking }
        else if activity.stationary { self = .stationary }
        else { self = .unknown }
    }
}
