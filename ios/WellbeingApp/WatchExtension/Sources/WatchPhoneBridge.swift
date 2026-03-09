import Foundation
import WatchConnectivity

final class WatchPhoneBridge: NSObject, WCSessionDelegate {
    static let shared = WatchPhoneBridge()

    var onStatusUpdate: ((String) -> Void)?

    private let workoutManager = WatchWorkoutManager.shared
    private var isStarted = false

    private override init() {
        super.init()
        workoutManager.onVitals = { [weak self] payload in
            self?.sendToPhone(payload)
        }
        workoutManager.onEvent = { [weak self] payload in
            self?.sendToPhone(payload)
        }
        workoutManager.onWorkoutStateChange = { [weak self] stateText in
            self?.onStatusUpdate?(stateText)
            self?.sendToPhone(["type": "workout_state", "state": stateText])
        }
    }

    func start() {
        guard WCSession.isSupported() else {
            onStatusUpdate?("WCSession unsupported")
            return
        }
        guard !isStarted else { return }
        isStarted = true

        let session = WCSession.default
        session.delegate = self
        session.activate()
        onStatusUpdate?("WCSession activating")
    }

    // MARK: - WCSessionDelegate

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        if let error {
            onStatusUpdate?("WC activate failed")
            print("Watch WCSession activation error: \(error)")
        } else {
            onStatusUpdate?("WC active")
        }
    }

    func sessionReachabilityDidChange(_ session: WCSession) {
        onStatusUpdate?(session.isReachable ? "Phone reachable" : "Phone not reachable")
    }

    func session(_ session: WCSession, didReceiveMessage message: [String : Any]) {
        handle(message: message, reply: nil)
    }

    func session(_ session: WCSession, didReceiveMessage message: [String : Any], replyHandler: @escaping ([String : Any]) -> Void) {
        handle(message: message, reply: replyHandler)
    }

    func session(_ session: WCSession, didReceiveUserInfo userInfo: [String : Any] = [:]) {
        handle(message: userInfo, reply: nil)
    }

    func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String : Any]) {
        handle(message: applicationContext, reply: nil)
    }

    // MARK: - Private

    private func handle(message: [String: Any], reply: (([String: Any]) -> Void)?) {
        print("WatchPhoneBridge: Received message: \(message)")
        let command = message["command"] as? String
        switch command {
        case "start_workout":
            print("WatchPhoneBridge: Handling start_workout command")
            let sessionKey = message["session_key"] as? String
            Task {
                do {
                    try await workoutManager.startIfNeeded(sessionKey: sessionKey)
                    print("WatchPhoneBridge: startIfNeeded completed successfully")
                    reply?(["ok": true])
                } catch {
                    print("WatchPhoneBridge: Watch workout start failed: \(error)")
                    reply?(["ok": false, "error": "\(error)"])
                }
            }
        case "stop_workout":
            print("WatchPhoneBridge: Handling stop_workout command")
            Task {
                do {
                    try await workoutManager.stopIfNeeded()
                    print("WatchPhoneBridge: stopIfNeeded completed successfully")
                    reply?(["ok": true])
                } catch {
                    print("WatchPhoneBridge: Watch workout stop failed: \(error)")
                    reply?(["ok": false, "error": "\(error)"])
                }
            }
        default:
            print("WatchPhoneBridge: Received unknown command: \(command ?? "nil")")
            reply?(["ok": false, "error": "unknown_command"]) 
        }
    }

    private func sendToPhone(_ message: [String: Any]) {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default

        if session.isReachable {
            session.sendMessage(message, replyHandler: nil) { error in
                print("Watch sendMessage error: \(error)")
            }
        } else {
            session.transferUserInfo(message)
        }
    }
}
