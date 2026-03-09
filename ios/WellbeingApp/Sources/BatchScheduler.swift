import Foundation
import UIKit

struct SessionMetricsSnapshot {
    var vitals: [Int: BatchItem] = [:]
    var events: [String: BatchItem] = [:]
    var counts: [String: Int] = [:]
    var latestEnvironmentalAudio: BatchItem?
    var latestAmbientNoise: BatchItem?
    var lastUpdated: Date?
}

actor SessionMetricsTracker {
    private var snapshot = SessionMetricsSnapshot()
    private let trackedVitalCodes: Set<Int> = [1, 2, 10, 20, 21, 40, 41, 42, 43, 44, 45]
    private let trackedEventLabels: Set<String> = ["motion_context", "session_marker", "audio_context"]

    func reset() {
        snapshot = SessionMetricsSnapshot()
    }

    func update(with items: [BatchItem]) {
        guard !items.isEmpty else { return }
        snapshot.lastUpdated = Date()
        for item in items {
            switch item.type {
            case .vital:
                guard let code = item.code, trackedVitalCodes.contains(code) else { continue }
                snapshot.vitals[code] = item
                let key = "vital_\(code)"
                snapshot.counts[key, default: 0] += 1
                if code == 10 {
                    if item.metadata?["source"]?.stringValue == "ambient_noise_manager" {
                        snapshot.latestAmbientNoise = item
                        snapshot.counts["vital_ambient_noise", default: 0] += 1
                    } else {
                        snapshot.latestEnvironmentalAudio = item
                        snapshot.counts["vital_environmental_audio", default: 0] += 1
                    }
                }
            case .event:
                guard let label = item.label, trackedEventLabels.contains(label) else { continue }
                snapshot.events[label] = item
                let key = "event_\(label)"
                snapshot.counts[key, default: 0] += 1
            default:
                continue
            }
        }
    }

    func currentSnapshot() -> SessionMetricsSnapshot {
        snapshot
    }
}

final class BatchScheduler: ObservableObject {
    enum Reason { case timer, appOpen, manual }

    @Published var isSessionActive: Bool = false
    @Published var runningSessionItems: [BatchItem] = []
    @Published var previousSessionItems: [BatchItem] = []
    @Published var currentSessionStartTime: Date?
    @Published var currentSessionEndTime: Date?
    @Published var previousSessionStartTime: Date?
    @Published var previousSessionEndTime: Date?
    @Published var watchWorkoutState: String = "Idle"
    private let interval: TimeInterval
    private var timer: Timer?
    private let api: APIClient
    private var sessionBuffer: [BatchItem] = []
    @Published private(set) var sessionMetricsSnapshot = SessionMetricsSnapshot()
    private let sessionMetricsTracker = SessionMetricsTracker()
    private let watchBridge = PhoneWatchBridge.shared
    private var sessionEndBackgroundTask: UIBackgroundTaskIdentifier = .invalid

    private static func resolveIngestURL() -> URL {
        if let urlString = Bundle.main.object(forInfoDictionaryKey: "APIBaseURL") as? String,
           let url = URL(string: urlString) {
            return url
        }
        return URL(string: "http://10.89.132.230:8000/ingest")!
    }

    init(intervalMinutes: Double) {
        self.interval = intervalMinutes * 60
        self.api = APIClient(baseURL: Self.resolveIngestURL(), userId: AppIdentity.userId, deviceId: DeviceId.value)

        watchBridge.onWorkoutStateUpdate = { [weak self] status in
            Task { @MainActor in
                self?.watchWorkoutState = status
            }
        }

        LocationManager.shared.onLocationBatchItem = { [weak self] item in
            Task { @MainActor in
                guard let self, self.isSessionActive else { return }
                self.sessionBuffer.append(item)
                self.runningSessionItems = self.sessionBuffer
            }
        }
    }

    func startStudySession() {
        print("BatchScheduler: startStudySession user_id=\(AppIdentity.userId) device_id=\(DeviceId.value)")

        // 1. Clear ambient buffered data
        try? LocalStore.shared.clear()

        // 2. Set session state immediately
        let startTime = Date()
        let sessionKey = StudySessionContext.startNewSession()
        isSessionActive = true
        currentSessionStartTime = startTime
        currentSessionEndTime = nil
        runningSessionItems = []
        sessionBuffer.removeAll()
        SensorCollector.shared.startSession(at: startTime)
        LocationManager.shared.beginSession(at: startTime)

        // 3. Log start marker immediately
        let startEvent = BatchItem(
            type: .event,
            t: startTime,
            label: "session_marker",
            val_text: "START",
            metadata: ["session_key": .string(sessionKey)]
        )
        try? LocalStore.shared.append(startEvent)
        sessionBuffer.append(startEvent)
        runningSessionItems = sessionBuffer

        // 4. Start high-frequency HealthKit workout session
        Task {
            await sessionMetricsTracker.reset()
            await MainActor.run { sessionMetricsSnapshot = SessionMetricsSnapshot() }
            try? await HealthKitManager.shared.startActiveSensingSession()
            _ = await collectSessionData(captureSession: true)
        }

        Task { [weak self] in
            guard let self else { return }
            let ok = await self.watchBridge.requestStartWorkout(sessionKey: sessionKey)
            if !ok {
                print("Watch workout start request was not acknowledged")
            }
        }

        resume()
    }

    func endStudySession() {
        guard isSessionActive else { return }
        print("BatchScheduler: endStudySession requested")

        // 1. End visible session state immediately so UI switches right away.
        let endTime = Date()
        let endEvent = BatchItem(
            type: .event,
            t: endTime,
            label: "session_marker",
            val_text: "END",
            metadata: StudySessionContext.stamp(metadata: nil)
        )
        try? LocalStore.shared.append(endEvent)

        var completedItems = sessionBuffer
        completedItems.append(endEvent)
        SensorCollector.shared.markSessionEnding(at: endTime)
        LocationManager.shared.endSession(at: endTime)

        isSessionActive = false
        stop()
        currentSessionEndTime = endTime
        previousSessionStartTime = currentSessionStartTime
        previousSessionEndTime = endTime
        previousSessionItems = completedItems
        runningSessionItems = []
        sessionBuffer = completedItems
        currentSessionStartTime = nil

        Task {
            await MainActor.run {
                beginSessionEndBackgroundTask()
            }
            print("BatchScheduler: endStudySession async cleanup started")

            // 2. Stop HealthKit session
            try? await HealthKitManager.shared.stopActiveSensingSession()

            // 3. Final collection and upload
            let collected = await collectSessionData(captureSession: true)
            print("BatchScheduler: final collection produced \(collected.count) items")
            let uploaded = await uploadPendingItems()
            print("BatchScheduler: final upload result=\(uploaded)")

            await MainActor.run {
                previousSessionItems = sessionBuffer
                sessionBuffer.removeAll()
                runningSessionItems = []
            }

            await sessionMetricsTracker.reset()
            await MainActor.run { sessionMetricsSnapshot = SessionMetricsSnapshot() }
            SensorCollector.shared.completeSession()
            StudySessionContext.clear()
            await MainActor.run {
                endSessionEndBackgroundTask()
            }
        }

        Task { [weak self] in
            guard let self else { return }
            let ok = await self.watchBridge.requestStopWorkout()
            if !ok {
                print("Watch workout stop request was not acknowledged")
            }
        }
    }

    func resume() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { _ = await self?.flushIfNeeded(reason: .timer) }
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    @discardableResult
    func flushIfNeeded(reason: Reason, captureSession: Bool = false) async -> Bool {
        print("BatchScheduler: flushIfNeeded reason=\(reason.label) captureSession=\(captureSession) isSessionActive=\(isSessionActive)")
        if reason == .appOpen {
            await api.probeConnectivity(context: reason.label)
        }
        let shouldCollect = isSessionActive || captureSession
        if shouldCollect {
            let collected = await collectSessionData(captureSession: captureSession)
            print("BatchScheduler: flush collected \(collected.count) items")
        }
        let uploaded = await uploadPendingItems()
        print("BatchScheduler: flush upload result=\(uploaded)")
        return uploaded
    }

    private func uploadPendingItems() async -> Bool {
        do {
            let pendingBefore = try LocalStore.shared.count()
            print("BatchScheduler: uploadPendingItems starting pending_count=\(pendingBefore)")
            while true {
                let items = try LocalStore.shared.drain(limit: 100)
                guard !items.isEmpty else {
                    print("BatchScheduler: uploadPendingItems drained 0 items, done")
                    return true
                }
                print("BatchScheduler: uploadPendingItems drained batch_count=\(items.count)")
                guard await api.send(items: items) else {
                    print("BatchScheduler: uploadPendingItems send failed, requeueing \(items.count) items")
                    try LocalStore.shared.append(items)
                    return false
                }
                print("BatchScheduler: uploadPendingItems send succeeded for \(items.count) items")
            }
        } catch {
            print("Upload failed: \(error)")
            return false
        }
    }

    private func collectSessionData(captureSession: Bool) async -> [BatchItem] {
        let collectedItems = await SensorCollector.shared.collect()
        print("BatchScheduler: collectSessionData captureSession=\(captureSession) collected=\(collectedItems.count)")
        let shouldTrack = isSessionActive || captureSession
        if shouldTrack {
            await MainActor.run {
                sessionBuffer += collectedItems
                runningSessionItems = sessionBuffer
            }
            await sessionMetricsTracker.update(with: collectedItems)
            let snapshot = await sessionMetricsTracker.currentSnapshot()
            await MainActor.run { sessionMetricsSnapshot = snapshot }
        }
        return collectedItems
    }

    @MainActor
    private func beginSessionEndBackgroundTask() {
        endSessionEndBackgroundTask()
        sessionEndBackgroundTask = UIApplication.shared.beginBackgroundTask(withName: "StudySessionUpload") { [weak self] in
            print("BatchScheduler: session-end background task expired")
            Task { @MainActor in
                self?.endSessionEndBackgroundTask()
            }
        }
        print("BatchScheduler: session-end background task started id=\(sessionEndBackgroundTask.rawValue)")
    }

    @MainActor
    private func endSessionEndBackgroundTask() {
        guard sessionEndBackgroundTask != .invalid else { return }
        print("BatchScheduler: session-end background task finished id=\(sessionEndBackgroundTask.rawValue)")
        UIApplication.shared.endBackgroundTask(sessionEndBackgroundTask)
        sessionEndBackgroundTask = .invalid
    }
}

private extension BatchScheduler.Reason {
    var label: String {
        switch self {
        case .timer:
            return "timer"
        case .appOpen:
            return "appOpen"
        case .manual:
            return "manual"
        }
    }
}
