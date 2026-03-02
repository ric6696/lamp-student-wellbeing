import Foundation
import WatchConnectivity

final class PhoneWatchBridge: NSObject, ObservableObject, WCSessionDelegate {
    static let shared = PhoneWatchBridge()

    var onWorkoutStateUpdate: ((String) -> Void)?

    @Published private(set) var isPaired: Bool = false
    @Published private(set) var isWatchAppInstalled: Bool = false
    @Published private(set) var isReachable: Bool = false
    @Published private(set) var activationStateRaw: Int = WCSessionActivationState.notActivated.rawValue

    private var isStarted = false
    private let iso = ISO8601DateFormatter()

    var connectivityText: String {
        if !isPaired { return "Watch not paired" }
        if !isWatchAppInstalled { return "Watch app not installed" }
        if isReachable { return "Watch connected" }
        return "Watch installed, currently not reachable"
    }

    var connectivityColorName: String {
        if !isPaired || !isWatchAppInstalled { return "red" }
        return isReachable ? "green" : "orange"
    }

    private override init() {
        super.init()
    }

    func start() {
        guard WCSession.isSupported() else {
            print("WCSession not supported on this device")
            return
        }
        guard !isStarted else { return }
        isStarted = true

        let session = WCSession.default
        session.delegate = self
        refreshSessionState(session)
        session.activate()
        print("WCSession activating")
    }

    func requestStartWorkout() async -> Bool {
        await sendCommandAwaitingAck("start_workout")
    }

    func requestStopWorkout() async -> Bool {
        await sendCommandAwaitingAck("stop_workout")
    }

    // MARK: - WCSessionDelegate

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        refreshSessionState(session)
        if let error {
            print("WCSession activation failed: \(error)")
        } else {
            print("WCSession activated: state=\(activationState.rawValue)")
        }
    }

    func sessionDidBecomeInactive(_ session: WCSession) {}

    func sessionDidDeactivate(_ session: WCSession) {
        refreshSessionState(session)
        session.activate()
    }

    func sessionReachabilityDidChange(_ session: WCSession) {
        refreshSessionState(session)
    }

    func sessionWatchStateDidChange(_ session: WCSession) {
        refreshSessionState(session)
    }

    func session(_ session: WCSession, didReceiveMessage message: [String : Any]) {
        handleIncoming(message)
    }

    func session(_ session: WCSession, didReceiveUserInfo userInfo: [String : Any] = [:]) {
        handleIncoming(userInfo)
    }

    func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String : Any]) {
        handleIncoming(applicationContext)
    }

    // MARK: - Private

    private func handleIncoming(_ message: [String: Any]) {
        guard let type = message["type"] as? String else { return }

        switch type {
        case "vitals":
            guard let items = message["items"] as? [[String: Any]] else { return }
            let date: Date
            if let t = message["t"] as? String, let parsed = iso.date(from: t) {
                date = parsed
            } else {
                date = Date()
            }

            let batchItems: [BatchItem] = items.compactMap { item in
                guard let code = item["code"] as? Int else { return nil }
                let val: Double?
                if let d = item["val"] as? Double { val = d }
                else if let n = item["val"] as? NSNumber { val = n.doubleValue }
                else { val = nil }
                guard let val else { return nil }
                return BatchItem(type: .vital, t: date, code: code, val: val, metadata: ["source": "watch"])
            }

            guard !batchItems.isEmpty else { return }
            Task { await HealthKitManager.shared.appendExternalVitals(batchItems) }

            // Implicitly confirm workout is active if vitals are arriving
            Task { @MainActor in
                self.onWorkoutStateUpdate?("Workout running (inferred)")
            }

        case "workout_state":
            if let state = message["state"] as? String {
                print("Watch workout state: \(state)")
                Task { @MainActor in
                    self.onWorkoutStateUpdate?(state)
                }
            }

        default:
            break
        }
    }

    private func sendCommandAwaitingAck(_ command: String) async -> Bool {
        guard WCSession.isSupported() else { return false }
        let session = WCSession.default

        if !isStarted {
            start()
        }

        guard session.isPaired, session.isWatchAppInstalled else {
            refreshSessionState(session)
            print("Watch not paired or watch app not installed")
            return false
        }

        if session.isReachable {
            refreshSessionState(session)
            return await withCheckedContinuation { cont in
                session.sendMessage(["command": command], replyHandler: { reply in
                    let ok = reply["ok"] as? Bool ?? false
                    cont.resume(returning: ok)
                }, errorHandler: { error in
                    print("sendMessage(\(command)) error: \(error)")
                    cont.resume(returning: false)
                })
            }
        } else {
            session.transferUserInfo(["command": command])
            do {
                try session.updateApplicationContext(["command": command])
            } catch {
                print("updateApplicationContext(\(command)) error: \(error)")
            }
            refreshSessionState(session)
            print("Watch not reachable; queued \(command) for delivery")
            return true
        }
    }

    private func refreshSessionState(_ session: WCSession) {
        Task { @MainActor in
            isPaired = session.isPaired
            isWatchAppInstalled = session.isWatchAppInstalled
            isReachable = session.isReachable
            activationStateRaw = session.activationState.rawValue
        }
    }
}
