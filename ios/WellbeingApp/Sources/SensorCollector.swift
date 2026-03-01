import Foundation
import HealthKit

final class SensorCollector {
    static let shared = SensorCollector()
    private init() {}

    private let vitalsLookbackOnColdStart: TimeInterval = 60 * 60
    private let vitalsOverlapWindow: TimeInterval = 10 * 60
    private let vitalMetrics: [(id: HKQuantityTypeIdentifier, code: Int)] = [
        (.heartRate, 1),
        (.heartRateVariabilitySDNN, 2),
        (.restingHeartRate, 3),
        (.environmentalAudioExposure, 10),
        (.stepCount, 20),
        (.activeEnergyBurned, 5),
        (.respiratoryRate, 30),
        (.distanceWalkingRunning, 21)
    ]

    func collect() async -> [BatchItem] {
        var collected: [BatchItem] = []
        collected += await collectVitals()
        collected += collectMotionContext()
        collected += await collectInstantNoise()
        return collected
    }

    private func collectInstantNoise() async -> [BatchItem] {
        let db = NoiseManager.shared.currentDb
        if db < 0 { return [] }
        let item = BatchItem(
            type: .vital,
            t: Date(),
            code: 10,
            val: Double(db),
            metadata: ["source": "ambient_noise_manager"]
        )
        try? LocalStore.shared.append(item)
        return [item]
    }

    private func collectVitals() async -> [BatchItem] {
        do {
            let queriedItems = try await withThrowingTaskGroup(of: [BatchItem].self) { group in
                for metric in vitalMetrics {
                    let key = syncKey(for: metric.code)
                    let lastSync = UserDefaults.standard.object(forKey: key) as? Date
                    let baseSince = lastSync ?? Date().addingTimeInterval(-vitalsLookbackOnColdStart)
                    let since = baseSince.addingTimeInterval(-vitalsOverlapWindow)
                    group.addTask {
                        try await HealthKitManager.shared.fetchRecentVitals(id: metric.id, metricCode: metric.code, since: since)
                    }
                }
                return try await group.reduce(into: []) { $0 += $1 }
            }

            let liveItems = await HealthKitManager.shared.consumeLiveVitals()
            let items = dedupeVitals(queriedItems + liveItems)
            try LocalStore.shared.append(items)

            for metric in vitalMetrics {
                if let latestForCode = items.filter({ $0.code == metric.code }).map(\.t).max() {
                    UserDefaults.standard.set(latestForCode, forKey: syncKey(for: metric.code))
                }
            }

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

    private func syncKey(for code: Int) -> String {
        "last_vitals_sync_code_\(code)"
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
}

