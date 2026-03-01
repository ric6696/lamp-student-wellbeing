import Foundation

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
    private let trackedVitalCodes: Set<Int> = [1, 2, 3, 5, 10, 20, 21, 30]
    private let trackedEventLabels: Set<String> = ["motion_context", "session_marker"]

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
                    if item.metadata?["source"] == "ambient_noise_manager" {
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
    private var sessionStartTime: Date?
    private let interval: TimeInterval
    private var timer: Timer?
    private let api: APIClient
    private var sessionBuffer: [BatchItem] = []
    @Published private(set) var sessionMetricsSnapshot = SessionMetricsSnapshot()
    private let sessionMetricsTracker = SessionMetricsTracker()

    init(intervalMinutes: Double) {
        self.interval = intervalMinutes * 60
        self.api = APIClient(baseURL: URL(string: "http://10.89.237.157:8000/ingest")!, deviceId: DeviceId.value)
    }

    func startStudySession() {
        // 1. Clear "Ambient" Data
        try? LocalStore.shared.clear()
        
        // 2. Set Session State
        self.isSessionActive = true
        self.sessionStartTime = Date()
        
        // 3. Start High-Freq HealthKit Workout Session
        Task {
            await sessionMetricsTracker.reset()
            await MainActor.run { sessionMetricsSnapshot = SessionMetricsSnapshot() }
            try? await HealthKitManager.shared.startActiveSensingSession()
            // Log Session Start Event
            let startEvent = BatchItem(type: .event, t: Date(), label: "session_marker", val_text: "START")
            try? LocalStore.shared.append(startEvent)
            await MainActor.run {
                sessionBuffer.removeAll()
                runningSessionItems = []
            }
            _ = await collectSessionData(captureSession: true)
        }
        
        resume()
    }

    func endStudySession() {
        self.isSessionActive = false
        stop()
        
        Task {
            // 1. Stop HealthKit Session
            try? await HealthKitManager.shared.stopActiveSensingSession()
            
            // 2. Log Session End Event
            let endEvent = BatchItem(type: .event, t: Date(), label: "session_marker", val_text: "END")
            try? LocalStore.shared.append(endEvent)
            
            // 3. Final collection and upload
            _ = await collectSessionData(captureSession: true)
            _ = await uploadPendingItems()
            
            await MainActor.run {
                previousSessionItems = sessionBuffer
                sessionBuffer.removeAll()
                runningSessionItems = []
            }
            
            self.sessionStartTime = nil
            await sessionMetricsTracker.reset()
            await MainActor.run { sessionMetricsSnapshot = SessionMetricsSnapshot() }
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
        let shouldCollect = isSessionActive || captureSession
        if shouldCollect {
            _ = await collectSessionData(captureSession: captureSession)
        }

        if reason == .timer || reason == .appOpen {
            return true
        }

        return await uploadPendingItems()
    }

    private func uploadPendingItems() async -> Bool {
        do {
            while true {
                let items = try LocalStore.shared.drain(limit: 100)
                guard !items.isEmpty else { return true }
                guard await api.send(items: items) else {
                    try LocalStore.shared.append(items)
                    return false
                }
            }
        } catch {
            print("Upload failed: \(error)")
            return false
        }
    }

    private func collectSessionData(captureSession: Bool) async -> [BatchItem] {
        let collectedItems = await SensorCollector.shared.collect()
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
}
