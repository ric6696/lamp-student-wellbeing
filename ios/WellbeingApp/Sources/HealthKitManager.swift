import Foundation
import HealthKit

final class HealthKitManager {
    static let shared = HealthKitManager()
    private let store = HKHealthStore()
    private init() {}

    func requestAuthorization() async {
        guard HKHealthStore.isHealthDataAvailable() else { return }
        let readTypes: Set = [
            HKQuantityType.quantityType(forIdentifier: .heartRate)!,
            HKQuantityType.quantityType(forIdentifier: .heartRateVariabilitySDNN)!,
            HKQuantityType.quantityType(forIdentifier: .restingHeartRate)!,
            HKQuantityType.quantityType(forIdentifier: .environmentalAudioExposure)!,
            HKQuantityType.quantityType(forIdentifier: .stepCount)!,
            HKQuantityType.quantityType(forIdentifier: .activeEnergyBurned)!,
            HKQuantityType.quantityType(forIdentifier: .appleExerciseTime)!,
            HKQuantityType.quantityType(forIdentifier: .appleStandTime)!,
            HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!
        ]
        try? await store.requestAuthorization(toShare: [], read: readTypes)
    }

    func fetchRecentVitals(since: Date) async throws -> [BatchItem] {
        try await withThrowingTaskGroup(of: [BatchItem].self) { group in
            let metrics: [(HKQuantityTypeIdentifier, Int)] = [
                (.heartRate, 1),
                (.heartRateVariabilitySDNN, 2),
                (.restingHeartRate, 3),
                (.environmentalAudioExposure, 10),
                (.stepCount, 20)
            ]
            for (id, metricCode) in metrics {
                group.addTask { try await self.queryQuantitySamples(id: id, metricCode: metricCode, since: since) }
            }
            return try await group.reduce(into: []) { $0 += $1 }
        }
    }

    func fetchSleepStages(since: Date) async throws -> [BatchItem] {
        let type = HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!
        let predicate = HKQuery.predicateForSamples(withStart: since, end: nil)

        return try await withCheckedThrowingContinuation { cont in
            let q = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, samples, err in
                if let err = err { cont.resume(throwing: err); return }
                let items: [BatchItem] = (samples as? [HKCategorySample] ?? []).compactMap { sample in
                    guard let stage = SleepStage.from(sample) else { return nil }
                    return BatchItem(
                        type: .event,
                        t: sample.startDate,
                        label: "sleep_stage",
                        val_text: stage.label,
                        metadata: ["stage_code": "\(stage.code)", "duration_sec": "\(Int(sample.endDate.timeIntervalSince(sample.startDate)))"]
                    )
                }
                cont.resume(returning: items)
            }
            store.execute(q)
        }
    }

    func fetchDailyAggregates(for date: Date) async throws -> DailyAggregates {
        let deviceId = DeviceId.value
        async let steps = sum(.stepCount, unit: .count(), on: date)
        async let energy = sum(.activeEnergyBurned, unit: .kilocalorie(), on: date)
        async let exercise = sum(.appleExerciseTime, unit: .minute(), on: date)
        async let stand = sum(.appleStandTime, unit: .minute(), on: date)
        async let sleep = sleepInterval(on: date)

        let (s, e, ex, st, sleepSpan) = try await (steps, energy, exercise, stand, sleep)

        let summary = DailySummary(
            date: date,
            device_id: deviceId,
            steps: Int(s),
            active_energy_kcal: e,
            exercise_min: Int(ex),
            sleep_start: sleepSpan?.0,
            sleep_end: sleepSpan?.1
        )

        return DailyAggregates(summary: summary, standMinutes: Int(st))
    }

    // MARK: - Private

    private func queryQuantitySamples(id: HKQuantityTypeIdentifier, metricCode: Int, since: Date) async throws -> [BatchItem] {
        let type = HKQuantityType.quantityType(forIdentifier: id)!
        let predicate = HKQuery.predicateForSamples(withStart: since, end: nil)
        return try await withCheckedThrowingContinuation { cont in
            let sort = NSSortDescriptor(key: HKSampleSortIdentifierEndDate, ascending: true)
            let q = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: [sort]) { _, samples, err in
                if let err = err { cont.resume(throwing: err); return }
                let items: [BatchItem] = (samples as? [HKQuantitySample] ?? []).map {
                    let value = $0.quantity.doubleValue(for: self.unit(for: id))
                    if value < 0 {
                        return nil
                    }
                    return BatchItem(
                        type: .vital,
                        t: $0.endDate,
                        code: metricCode,
                        val: value
                    )
                }
                cont.resume(returning: items.compactMap { $0 })
            }
            store.execute(q)
        }
    }

    private func unit(for id: HKQuantityTypeIdentifier) -> HKUnit {
        switch id {
        case .heartRate: return HKUnit.count().unitDivided(by: .minute())
        case .heartRateVariabilitySDNN: return HKUnit.secondUnit(with: .milli)
        case .restingHeartRate: return HKUnit.count().unitDivided(by: .minute())
        case .environmentalAudioExposure: return .decibelAWeightedSoundPressureLevel()
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

    private func sleepInterval(on date: Date) async throws -> (Date, Date)? {
        let type = HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!
        return try await withCheckedThrowingContinuation { cont in
            let start = Calendar.current.startOfDay(for: date)
            let end = Calendar.current.date(byAdding: .day, value: 1, to: start)!
            let predicate = HKQuery.predicateForSamples(withStart: start, end: end)
            let sort = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)
            let q = HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: [sort]) { _, samples, err in
                if let err = err { cont.resume(throwing: err); return }
                guard let first = (samples as? [HKCategorySample])?.first else { cont.resume(returning: nil); return }
                let last = (samples as? [HKCategorySample])?.last
                cont.resume(returning: (first.startDate, last?.endDate ?? first.endDate))
            }
            store.execute(q)
        }
    }
}

struct SleepStage {
    let label: String
    let code: Int

    static func from(_ sample: HKCategorySample) -> SleepStage? {
        let value = sample.value
        if #available(iOS 16.0, *) {
            switch value {
            case HKCategoryValueSleepAnalysis.awake.rawValue:
                return SleepStage(label: "awake", code: value)
            case HKCategoryValueSleepAnalysis.asleepREM.rawValue:
                return SleepStage(label: "rem", code: value)
            case HKCategoryValueSleepAnalysis.asleepCore.rawValue:
                return SleepStage(label: "core", code: value)
            case HKCategoryValueSleepAnalysis.asleepDeep.rawValue:
                return SleepStage(label: "deep", code: value)
            default:
                return nil
            }
        } else {
            switch value {
            case HKCategoryValueSleepAnalysis.awake.rawValue:
                return SleepStage(label: "awake", code: value)
            default:
                return nil
            }
        }
    }
}
