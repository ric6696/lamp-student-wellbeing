import Foundation

final class SensorCollector {
    static let shared = SensorCollector()
    private init() {}

    private let lastVitalsKey = "last_vitals_sync"
    private let lastSleepKey = "last_sleep_sync"
    private let lastDailyKey = "last_daily_sync"

    func collect() async {
        await collectVitals()
        await collectSleepStages()
        await collectDailyAggregatesIfNeeded()
        await collectInstantNoise()
    }

    private func collectInstantNoise() async {
        let db = NoiseManager.shared.currentDb
        let item = BatchItem(
            type: .vital,
            t: Date(),
            code: 10,
            val: Double(db)
        )
        try? LocalStore.shared.append(item)
    }

    private func collectVitals() async {
        let since = UserDefaults.standard.object(forKey: lastVitalsKey) as? Date ?? Date().addingTimeInterval(-3600)
        do {
            let items = try await HealthKitManager.shared.fetchRecentVitals(since: since)
            try LocalStore.shared.append(items)
            UserDefaults.standard.set(Date(), forKey: lastVitalsKey)
        } catch {
            print("Vitals collection failed: \(error)")
        }
    }

    private func collectSleepStages() async {
        let since = UserDefaults.standard.object(forKey: lastSleepKey) as? Date ?? Date().addingTimeInterval(-24 * 3600)
        do {
            let items = try await HealthKitManager.shared.fetchSleepStages(since: since)
            try LocalStore.shared.append(items)
            UserDefaults.standard.set(Date(), forKey: lastSleepKey)
        } catch {
            print("Sleep collection failed: \(error)")
        }
    }

    private func collectDailyAggregatesIfNeeded() async {
        let today = Calendar.current.startOfDay(for: Date())
        let lastDate = UserDefaults.standard.object(forKey: lastDailyKey) as? Date
        guard lastDate != today else { return }

        do {
            let aggregates = try await HealthKitManager.shared.fetchDailyAggregates(for: today)
            let summary = aggregates.summary
            let item = BatchItem(
                type: .event,
                t: summary.date,
                label: "daily_summary",
                val_text: nil,
                metadata: [
                    "steps": "\(summary.steps)",
                    "active_energy_kcal": "\(summary.active_energy_kcal)",
                    "exercise_min": "\(summary.exercise_min)",
                    "sleep_start": summary.sleep_start?.iso8601String() ?? "",
                    "sleep_end": summary.sleep_end?.iso8601String() ?? ""
                ]
            )
            try LocalStore.shared.append(item)

            if aggregates.standMinutes > 0 {
                let standEvent = BatchItem(
                    type: .event,
                    t: summary.date,
                    label: "stand_minutes",
                    val_text: "\(aggregates.standMinutes)",
                    metadata: nil
                )
                try LocalStore.shared.append(standEvent)
            }

            UserDefaults.standard.set(today, forKey: lastDailyKey)
        } catch {
            print("Daily aggregate collection failed: \(error)")
        }
    }
}

private extension Date {
    func iso8601String() -> String {
        ISO8601DateFormatter().string(from: self)
    }
}
