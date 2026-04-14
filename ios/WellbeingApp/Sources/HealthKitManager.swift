import Foundation
import HealthKit

final class HealthKitManager {
    static let shared = HealthKitManager()
    private let store = HKHealthStore()
    private let liveVitalsBuffer = LiveVitalsBuffer()
    
    private init() {}

    func requestAuthorization() async {
        guard HKHealthStore.isHealthDataAvailable() else { return }
        let shareTypes: Set = [
            HKObjectType.workoutType()
        ]
        let readTypes: Set = [
            HKQuantityType.quantityType(forIdentifier: .heartRate)!,
            HKQuantityType.quantityType(forIdentifier: .heartRateVariabilitySDNN)!,
            HKQuantityType.quantityType(forIdentifier: .environmentalAudioExposure)!,
            HKQuantityType.quantityType(forIdentifier: .stepCount)!,
            HKQuantityType.quantityType(forIdentifier: .distanceWalkingRunning)!
        ]
        do {
            try await store.requestAuthorization(toShare: shareTypes, read: readTypes)
        } catch {
            print("HealthKit authorization failed: \(error)")
        }

        let hrStatus = store.authorizationStatus(for: HKQuantityType.quantityType(forIdentifier: .heartRate)!)
        let workoutShareStatus = store.authorizationStatus(for: HKObjectType.workoutType())
        print("HealthKit auth status - heartRate: \(hrStatus.rawValue), workoutShare: \(workoutShareStatus.rawValue)")
    }

    func startActiveSensingSession() async throws {
        // HKWorkoutSession / HKLiveWorkoutBuilder are not available on iOS.
        // On iPhone we rely on periodic queries + any externally appended vitals.
    }

    func stopActiveSensingSession() async throws {
        // No-op on iOS.
    }

    func fetchRecentVitals(since: Date) async throws -> [BatchItem] {
        try await withThrowingTaskGroup(of: [BatchItem].self) { group in
            let metrics: [(HKQuantityTypeIdentifier, Int)] = [
                (.heartRate, 1),
                (.heartRateVariabilitySDNN, 2),
                (.environmentalAudioExposure, 10),
                (.stepCount, 20),
                (.distanceWalkingRunning, 21)
            ]
            let s = self
            for (id, metricCode) in metrics {
                group.addTask { try await s.queryQuantitySamples(id: id, metricCode: metricCode, since: since) }
            }
            let items = try await group.reduce(into: []) { $0 += $1 }
            let counts = Dictionary(grouping: items, by: { $0.code ?? -1 }).mapValues(\.count)
            print("HealthKit vitals fetched: total=\(items.count), byCode=\(counts), since=\(since)")
            return items
        }
    }

    func fetchRecentVitals(id: HKQuantityTypeIdentifier, metricCode: Int, since: Date) async throws -> [BatchItem] {
        let items = try await queryQuantitySamples(id: id, metricCode: metricCode, since: since)
        print("HealthKit metric fetched: code=\(metricCode), samples=\(items.count), since=\(since)")
        return items
    }

    func consumeLiveVitals() async -> [BatchItem] {
        await liveVitalsBuffer.drain()
    }

    func appendExternalVitals(_ items: [BatchItem]) async {
        guard !items.isEmpty else { return }
        await liveVitalsBuffer.append(items)
    }

    func fetchSleepStages(since: Date) async throws -> [BatchItem] {
        // let type = HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!
        // let predicate = HKQuery.predicateForSamples(withStart: since, end: nil)

        return try await withCheckedThrowingContinuation { cont in
            cont.resume(returning: [])
        }
    }

    func fetchDailyAggregates(for date: Date) async throws -> DailyAggregates {
        let deviceId = DeviceId.value
        async let steps = sum(.stepCount, unit: .count(), on: date)
        let s = try await steps

        let summary = DailySummary(
            date: date,
            device_id: deviceId,
            steps: Int(s),
            active_energy_kcal: 0,
            exercise_min: 0,
            sleep_start: nil,
            sleep_end: nil
        )

        return DailyAggregates(summary: summary, standMinutes: 0)
    }

    // MARK: - Private

    private func queryQuantitySamples(id: HKQuantityTypeIdentifier, metricCode: Int, since: Date) async throws -> [BatchItem] {
        let type = HKQuantityType.quantityType(forIdentifier: id)!
        let predicate = HKQuery.predicateForSamples(withStart: since, end: nil)
        let unit = unit(for: id)
        return try await withCheckedThrowingContinuation { cont in
            let sort = NSSortDescriptor(key: HKSampleSortIdentifierEndDate, ascending: true)
            let q = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: [sort]) { _, samples, err in
                if let err = err { cont.resume(throwing: err); return }
                let items: [BatchItem] = (samples as? [HKQuantitySample] ?? []).compactMap { sample in
                    let value = sample.quantity.doubleValue(for: unit)
                    if value < 0 {
                        return nil
                    }
                    return BatchItem(
                        type: .vital,
                        t: sample.endDate,
                        code: metricCode,
                        val: value
                    )
                }
                cont.resume(returning: items)
            }
            store.execute(q)
        }
    }

    private func unit(for id: HKQuantityTypeIdentifier) -> HKUnit {
        switch id {
        case .heartRate: return HKUnit.count().unitDivided(by: .minute())
        case .heartRateVariabilitySDNN: return .secondUnit(with: .milli)
        case .environmentalAudioExposure: return .decibelAWeightedSoundPressureLevel()
        case .distanceWalkingRunning: return .meter()
        default: return .count()
        }
    }

    private func sum(_ id: HKQuantityTypeIdentifier, unit: HKUnit, on date: Date) async throws -> Double {
        let type = HKQuantityType.quantityType(forIdentifier: id)!
        return try await withCheckedThrowingContinuation { cont in
            let start = Calendar.current.startOfDay(for: date)
            let end = Calendar.current.date(byAdding: .day, value: 1, to: start)!
            let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: .strictStartDate)
            let q = HKStatisticsQuery(quantityType: type, quantitySamplePredicate: predicate, options: .cumulativeSum) { _, stats, err in
                if let err = err { cont.resume(throwing: err); return }
                cont.resume(returning: stats?.sumQuantity()?.doubleValue(for: unit) ?? 0)
            }
            store.execute(q)
        }
    }
}

actor LiveVitalsBuffer {
    private var items: [BatchItem] = []

    func append(_ newItems: [BatchItem]) {
        items += newItems
    }

    func drain() -> [BatchItem] {
        defer { items.removeAll() }
        return items
    }
}
