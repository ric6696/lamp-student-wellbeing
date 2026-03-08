import Foundation
import HealthKit

private struct SessionWindow {
    let start: Date
    let end: Date?

    func contains(_ date: Date) -> Bool {
        if date < start { return false }
        if let end, date > end { return false }
        return true
    }
}

final class SensorCollector {
    static let shared = SensorCollector()
    private init() {}

    private let vitalsOverlapWindow: TimeInterval = 10 * 60
    private let sessionLock = DispatchQueue(label: "sensor.collector.session")
    private var sessionStartDate: Date?
    private var sessionEndDate: Date?
    private var lastSampleDateByCode: [Int: Date] = [:]
    private let vitalMetrics: [(id: HKQuantityTypeIdentifier, code: Int)] = [
        (.heartRate, 1),
        (.environmentalAudioExposure, 10),
        (.stepCount, 20),
        (.distanceWalkingRunning, 21)
    ]

    func startSession(at date: Date) {
        sessionLock.sync {
            sessionStartDate = date
            sessionEndDate = nil
            lastSampleDateByCode.removeAll()
        }
    }

    func markSessionEnding(at date: Date) {
        sessionLock.sync {
            sessionEndDate = date
        }
    }

    func completeSession() {
        sessionLock.sync {
            sessionStartDate = nil
            sessionEndDate = nil
            lastSampleDateByCode.removeAll()
        }
    }

    func collect() async -> [BatchItem] {
        guard sessionWindow() != nil else { return [] }
        var collected: [BatchItem] = []
        collected += await collectVitals()
        collected += collectMotionContext()
        collected += collectAudioContext()
        collected += await collectInstantNoise()
        return collected
    }

    private func collectInstantNoise() async -> [BatchItem] {
        guard let window = sessionWindow(), window.contains(Date()) else { return [] }
        let db = NoiseManager.shared.currentDb
        let mappedDbA = max(0.0, min(120.0, Double(db) + 120.0))
        let item = BatchItem(
            type: .vital,
            t: Date(),
            code: 10,
            val: mappedDbA,
            metadata: [
                "source": .string("ambient_noise_manager"),
                "raw_dbfs": .number(Double(db)),
                "mapped_dba": .number(mappedDbA)
            ]
        )
        try? LocalStore.shared.append(item)
        return [item]
    }

    private func collectVitals() async -> [BatchItem] {
        guard let window = sessionWindow() else { return [] }
        do {
            let queriedItems = try await withThrowingTaskGroup(of: [BatchItem].self) { group in
                for metric in vitalMetrics {
                    let since = sinceDate(for: metric.code, within: window)
                    group.addTask {
                        try await HealthKitManager.shared.fetchRecentVitals(id: metric.id, metricCode: metric.code, since: since)
                    }
                }
                return try await group.reduce(into: []) { $0 += $1 }
            }

            let liveItems = await HealthKitManager.shared.consumeLiveVitals().filter { window.contains($0.t) }
            let items = dedupeVitals((queriedItems + liveItems).filter { window.contains($0.t) })
            try LocalStore.shared.append(items)
            updateLastSampleDates(with: items)

            if items.isEmpty {
                print("Vitals collection: 0 samples")
            } else {
                let counts = Dictionary(grouping: items, by: { $0.code ?? -1 }).mapValues(\.count)
                print("Vitals collection: \(items.count) samples byCode=\(counts)")
            }
            return items
        } catch {
            print("Vitals collection failed: \(error)")
            return []
        }
    }

    private func dedupeVitals(_ items: [BatchItem]) -> [BatchItem] {
        var seen = Set<String>()
        var output: [BatchItem] = []
        let iso = ISO8601DateFormatter()

        for item in items {
            guard let code = item.code, let value = item.val else { continue }
            let rounded = (value * 100).rounded() / 100
            let key = "\(code)|\(iso.string(from: item.t))|\(rounded)"
            if seen.contains(key) { continue }
            seen.insert(key)
            output.append(item)
        }
        return output
    }

    private func collectMotionContext() -> [BatchItem] {
        guard let window = sessionWindow(), window.contains(Date()) else { return [] }
        let context = MotionManager.shared.currentContext
        let item = BatchItem(
            type: .event,
            t: Date(),
            motion_context: context.rawValue,
            label: "motion_context",
            val_text: context.rawValue
        )
        try? LocalStore.shared.append(item)
        return [item]
    }

    private func collectAudioContext() -> [BatchItem] {
        guard let snapshot = NoiseManager.shared.latestAudioContext() else {
            return []
        }
        guard let window = sessionWindow(), window.contains(snapshot.timestamp) else {
            return []
        }

        var metadata: [String: JSONValue] = [
            "confidence": .number(snapshot.confidence),
            "db": .number(Double(snapshot.decibel)),
            "label_source": .string(snapshot.labelSource),
            "heuristic_label": .string(snapshot.heuristicLabel)
        ]
        if let aiLabel = snapshot.aiLabel {
            metadata["ai_label"] = .string(aiLabel)
        }
        if let aiConfidence = snapshot.aiConfidence {
            metadata["ai_confidence"] = .number(aiConfidence)
        }

        let item = BatchItem(
            type: .event,
            t: snapshot.timestamp,
            label: "audio_context",
            val_text: snapshot.label,
            metadata: metadata
        )
        try? LocalStore.shared.append(item)
        return [item]
    }
}

// MARK: - Session helpers

private extension SensorCollector {
    func sessionWindow() -> SessionWindow? {
        sessionLock.sync {
            guard let start = sessionStartDate else { return nil }
            return SessionWindow(start: start, end: sessionEndDate)
        }
    }

    func sinceDate(for code: Int, within window: SessionWindow) -> Date {
        let previous = sessionLock.sync { lastSampleDateByCode[code] }
        let candidate = previous?.addingTimeInterval(-vitalsOverlapWindow)
        return candidate.map { max($0, window.start) } ?? window.start
    }

    func updateLastSampleDates(with items: [BatchItem]) {
        guard !items.isEmpty else { return }
        sessionLock.sync {
            for item in items {
                guard let code = item.code else { continue }
                if let current = lastSampleDateByCode[code] {
                    if item.t > current {
                        lastSampleDateByCode[code] = item.t
                    }
                } else {
                    lastSampleDateByCode[code] = item.t
                }
            }
        }
    }
}

