import Foundation
import HealthKit

final class HealthKitManager {
    static let shared = HealthKitManager()
    private let store = HKHealthStore()
    private let workoutCoordinator = WorkoutLifecycleCoordinator()
    private let liveVitalsBuffer = LiveVitalsBuffer()
    
    private init() {
        workoutCoordinator.onLiveVitals = { [weak self] items in
            guard let self = self else { return }
            Task { await self.liveVitalsBuffer.append(items) }
        }
    }

    func requestAuthorization() async {
        guard HKHealthStore.isHealthDataAvailable() else { return }
        let shareTypes: Set = [
            HKObjectType.workoutType()
        ]
        let readTypes: Set = [
            HKQuantityType.quantityType(forIdentifier: .heartRate)!,
            HKQuantityType.quantityType(forIdentifier: .heartRateVariabilitySDNN)!,
            HKQuantityType.quantityType(forIdentifier: .restingHeartRate)!,
            HKQuantityType.quantityType(forIdentifier: .environmentalAudioExposure)!,
            HKQuantityType.quantityType(forIdentifier: .stepCount)!,
            HKQuantityType.quantityType(forIdentifier: .activeEnergyBurned)!,
            HKQuantityType.quantityType(forIdentifier: .appleExerciseTime)!,
            HKQuantityType.quantityType(forIdentifier: .appleStandTime)!,
            HKQuantityType.quantityType(forIdentifier: .distanceWalkingRunning)!,
            HKQuantityType.quantityType(forIdentifier: .respiratoryRate)!,
            HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!
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
        if #available(iOS 17.0, *) {
            try await workoutCoordinator.startIfNeeded(on: store)
        }
    }

    func stopActiveSensingSession() async throws {
        if #available(iOS 17.0, *) {
            try await workoutCoordinator.stopIfNeeded()
        }
    }

    func fetchRecentVitals(since: Date) async throws -> [BatchItem] {
        try await withThrowingTaskGroup(of: [BatchItem].self) { group in
            let metrics: [(HKQuantityTypeIdentifier, Int)] = [
                (.heartRate, 1),
                (.heartRateVariabilitySDNN, 2),
                (.restingHeartRate, 3),
                (.environmentalAudioExposure, 10),
                (.stepCount, 20),
                (.activeEnergyBurned, 5),
                (.respiratoryRate, 30),
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
                let items: [BatchItem] = (samples as? [HKQuantitySample] ?? []).compactMap { sample in
                    let value = sample.quantity.doubleValue(for: self.unit(for: id))
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
        case .heartRateVariabilitySDNN: return HKUnit.secondUnit(with: .milli)
        case .restingHeartRate: return HKUnit.count().unitDivided(by: .minute())
        case .environmentalAudioExposure: return .decibelAWeightedSoundPressureLevel()
        case .respiratoryRate: return HKUnit.count().unitDivided(by: .minute())
        case .activeEnergyBurned: return .kilocalorie()
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

final class WorkoutLifecycleCoordinator: NSObject, HKWorkoutSessionDelegate, HKLiveWorkoutBuilderDelegate {
    private var session: HKWorkoutSession?
    private var builder: HKLiveWorkoutBuilder?
    private var isStarting = false
    private var isStopping = false
    private var isFinalizing = false
    private var stopContinuation: CheckedContinuation<Void, Error>?
    var onLiveVitals: (([BatchItem]) -> Void)?

    func startIfNeeded(on healthStore: HKHealthStore) async throws {
        guard session == nil, builder == nil, !isStarting else {
            print("Workout start ignored: session already starting/running")
            return
        }

        isStarting = true
        defer { isStarting = false }

        do {
            let configuration = HKWorkoutConfiguration()
            configuration.activityType = .other
            configuration.locationType = .indoor

            let session = try HKWorkoutSession(healthStore: healthStore, configuration: configuration)
            let builder = session.associatedWorkoutBuilder()
            session.delegate = self
            builder.delegate = self
            builder.dataSource = HKLiveWorkoutDataSource(healthStore: healthStore, workoutConfiguration: configuration)

            self.session = session
            self.builder = builder

            session.prepare()

            let startDate = Date()
            session.startActivity(with: startDate)
            try await builder.beginCollection(at: startDate)
            print("Workout live collection started")
        } catch {
            print("Workout start failed: \(error)")
            cleanupAndReset()
            throw error
        }
    }

    func stopIfNeeded() async throws {
        guard let session = session else {
            return
        }
        guard !isStopping else {
            print("Workout stop ignored: already stopping")
            return
        }

        isStopping = true
        isFinalizing = false
        session.stopActivity(with: Date())

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            stopContinuation = continuation
        }
    }

    func workoutSession(_ workoutSession: HKWorkoutSession, didFailWithError error: Error) {
        print("WorkoutSession failed: \(error)")
        completeStop(error: error)
        cleanupAndReset()
    }

    func workoutSession(_ workoutSession: HKWorkoutSession,
                        didChangeTo toState: HKWorkoutSessionState,
                        from fromState: HKWorkoutSessionState,
                        date: Date) {
        if toState == .stopped || toState == .ended {
            finalizeStopFlow(endDate: date)
        }
    }

    func workoutBuilderDidCollectEvent(_ workoutBuilder: HKLiveWorkoutBuilder) {}

    func workoutBuilder(_ workoutBuilder: HKLiveWorkoutBuilder, didCollectDataOf collectedTypes: Set<HKSampleType>) {
        let liveMappings: [(HKQuantityTypeIdentifier, Int)] = [
            (.heartRate, 1),
            (.heartRateVariabilitySDNN, 2),
            (.restingHeartRate, 3),
            (.activeEnergyBurned, 5),
            (.environmentalAudioExposure, 10),
            (.stepCount, 20),
            (.distanceWalkingRunning, 21),
            (.respiratoryRate, 30)
        ]

        var items: [BatchItem] = []
        for (identifier, code) in liveMappings {
            guard let quantityType = HKQuantityType.quantityType(forIdentifier: identifier) else { continue }
            guard collectedTypes.contains(quantityType) else { continue }
            guard let stats = workoutBuilder.statistics(for: quantityType),
                  let quantity = stats.mostRecentQuantity() else { continue }

            let value = quantity.doubleValue(for: unit(for: identifier))
            if value < 0 { continue }
            items.append(BatchItem(type: .vital, t: Date(), code: code, val: value))
        }

        if !items.isEmpty {
            onLiveVitals?(items)
        }
    }

    private func unit(for id: HKQuantityTypeIdentifier) -> HKUnit {
        switch id {
        case .heartRate: return HKUnit.count().unitDivided(by: .minute())
        case .heartRateVariabilitySDNN: return HKUnit.secondUnit(with: .milli)
        case .restingHeartRate: return HKUnit.count().unitDivided(by: .minute())
        case .environmentalAudioExposure: return .decibelAWeightedSoundPressureLevel()
        case .respiratoryRate: return HKUnit.count().unitDivided(by: .minute())
        case .activeEnergyBurned: return .kilocalorie()
        case .distanceWalkingRunning: return .meter()
        default: return .count()
        }
    }

    private func finalizeStopFlow(endDate: Date) {
        guard !isFinalizing else { return }
        isFinalizing = true

        guard let builder = builder, let session = session else {
            completeStop(error: nil)
            cleanupAndReset()
            return
        }

        builder.endCollection(withEnd: endDate) { [weak self] _, endError in
            guard let self = self else { return }
            if let endError {
                print("endCollection error: \(endError)")
                self.completeStop(error: endError)
                self.cleanupAndReset()
                return
            }

            builder.finishWorkout { _, finishError in
                if let finishError {
                    print("finishWorkout error: \(finishError)")
                }
                session.end()
                self.completeStop(error: finishError)
                self.cleanupAndReset()
            }
        }
    }

    private func completeStop(error: Error?) {
        guard let continuation = stopContinuation else {
            isStopping = false
            return
        }
        stopContinuation = nil
        isStopping = false

        if let error {
            continuation.resume(throwing: error)
        } else {
            continuation.resume()
        }
    }

    private func cleanupAndReset() {
        session = nil
        builder = nil
        stopContinuation = nil
        isStarting = false
        isStopping = false
        isFinalizing = false
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
