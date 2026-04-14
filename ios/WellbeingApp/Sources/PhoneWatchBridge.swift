import Foundation
import WatchConnectivity

final class PhoneWatchBridge: NSObject, ObservableObject, WCSessionDelegate {
    static let shared = PhoneWatchBridge()

    var onWorkoutStateUpdate: ((String) -> Void)?

    @Published private(set) var isPaired: Bool = false
    @Published private(set) var isWatchAppInstalled: Bool = false
    @Published private(set) var isReachable: Bool = false
    @Published private(set) var activationStateRaw: Int = WCSessionActivationState.notActivated.rawValue
    @Published var enableWatchDataCollection: Bool = true

    private var isStarted = false
    private let iso = ISO8601DateFormatter()

    var connectivityText: String {
        if !isPaired { return "Watch not paired" }
        if !isWatchAppInstalled { return "Watch app not installed" }
        if isReachable { return "Watch connected" }
        return "Waiting for connection"
    }

    var connectivityColorName: String {
        if !isPaired || !isWatchAppInstalled { return "red" }
        return isReachable ? "green" : "orange"
    }

    var currentConnectionStep: Int {
        if !isPaired || !isWatchAppInstalled { return 0 }
        if !isReachable { return 1 }
        return 3
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

    func requestStartWorkout(sessionKey: String) async -> Bool {
        await sendCommandAwaitingAck("start_workout", sessionKey: sessionKey)
    }

    func requestStopWorkout() async -> Bool {
        await sendCommandAwaitingAck("stop_workout", sessionKey: nil)
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
            guard let sourceDeviceId = message["device_id"] as? String, !sourceDeviceId.isEmpty else { return }
            let sessionKey = message["session_key"] as? String
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
                var metadata: [String: JSONValue] = ["source": .string("watch")]
                if let sessionKey, !sessionKey.isEmpty {
                    metadata["session_key"] = .string(sessionKey)
                }
                return BatchItem(
                    device_id: sourceDeviceId,
                    type: .vital,
                    t: date,
                    code: code,
                    val: val,
                    metadata: metadata
                )
            }

            guard !batchItems.isEmpty else { return }
            Task { await HealthKitManager.shared.appendExternalVitals(batchItems) }

            // Implicitly confirm workout is active if vitals are arriving
            Task { @MainActor in
                self.onWorkoutStateUpdate?("Workout running (inferred)")
            }

        case "event":
            guard let sourceDeviceId = message["device_id"] as? String, !sourceDeviceId.isEmpty else { return }
            guard let label = message["label"] as? String else { return }

            let date: Date
            if let t = message["t"] as? String, let parsed = iso.date(from: t) {
                date = parsed
            } else {
                date = Date()
            }

            let metadata = (message["metadata"] as? [String: Any])?.compactMapValues { self.jsonValue(from: $0) }
            let item = BatchItem(
                device_id: sourceDeviceId,
                type: .event,
                t: date,
                label: label,
                val_text: message["val_text"] as? String,
                metadata: metadata
            )
            try? LocalStore.shared.append(item)

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

    private func sendCommandAwaitingAck(_ command: String, sessionKey: String?) async -> Bool {
        guard WCSession.isSupported() else { return false }
        let session = WCSession.default
        var payload: [String: Any] = ["command": command]
        if let sessionKey, !sessionKey.isEmpty {
            payload["session_key"] = sessionKey
        }

        if !isStarted {
            start()
        }

        guard session.isPaired, session.isWatchAppInstalled else {
            refreshSessionState(session)
            print("PhoneWatchBridge: skipping \(command); watch not paired or app not installed")
            return true
        }

        if session.isReachable {
            refreshSessionState(session)
            return await withCheckedContinuation { cont in
                session.sendMessage(payload, replyHandler: { reply in
                    let ok = reply["ok"] as? Bool ?? false
                    cont.resume(returning: ok)
                }, errorHandler: { error in
                    print("sendMessage(\(command)) error: \(error)")
                    cont.resume(returning: false)
                })
            }
        } else {
            session.transferUserInfo(payload)
            do {
                try session.updateApplicationContext(payload)
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

    private func jsonValue(from raw: Any) -> JSONValue? {
        switch raw {
        case let value as String:
            return .string(value)
        case let value as NSNumber:
            if CFGetTypeID(value) == CFBooleanGetTypeID() {
                return .bool(value.boolValue)
            }
            return .number(value.doubleValue)
        default:
            return nil
        }
    }
}
