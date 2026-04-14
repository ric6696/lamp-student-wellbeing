import Foundation
import HealthKit
import CoreMotion

final class WatchWorkoutManager: NSObject, HKWorkoutSessionDelegate, HKLiveWorkoutBuilderDelegate {
    static let shared = WatchWorkoutManager()

    var onVitals: (([String: Any]) -> Void)?
    var onEvent: (([String: Any]) -> Void)?
    var onWorkoutStateChange: ((String) -> Void)?

    private let store = HKHealthStore()
    private var session: HKWorkoutSession?
    private var builder: HKLiveWorkoutBuilder?

    private var isStarting = false
    private var isStopping = false
    private var isFinalizing = false
    private var stopContinuation: CheckedContinuation<Void, Error>?
    private var currentSessionKey: String?
    private var lastEmittedMotionContext: String?

    private var lastVitalsSentAt: Date?
    private let motionManager = CMMotionManager()
    private let activityManager = CMMotionActivityManager()
    
    private let motionQueue: OperationQueue = {
        let q = OperationQueue()
        q.name = "watch.motion.queue"
        return q
    }()
    
    // Accumulators for high-freq motion data
    private var accelMagnitudeWindow: [Double] = []
    private var gyroXWindow: [Double] = []
    private var gyroYWindow: [Double] = []
    private var gyroZWindow: [Double] = []
    
    // Latest low-freq context
    private var latestActivityCode: Int = 0
    
    private let motionLock = NSLock()

    private override init() {
        super.init()
    }

    func startIfNeeded(sessionKey: String?) async throws {
        print("WatchWorkoutManager: startIfNeeded called")
        guard session == nil, builder == nil, !isStarting else {
            let reason = session != nil ? "session exists" : (builder != nil ? "builder exists" : "isStarting")
            print("WatchWorkoutManager: Workout start ignored because: \(reason)")
            onWorkoutStateChange?("Workout already running")
            return
        }

        isStarting = true
        defer { isStarting = false }
        currentSessionKey = sessionKey

        print("WatchWorkoutManager: Requesting authorization...")
        do {
            try await requestAuthorizationIfNeeded()
        } catch {
            print("WatchWorkoutManager: Auth request threw: \(error)")
            onWorkoutStateChange?("Auth failed")
            throw error
        }

        do {
            print("WatchWorkoutManager: Creating HKWorkoutSession...")
            let configuration = HKWorkoutConfiguration()
            configuration.activityType = .other
            configuration.locationType = .indoor

            let session = try HKWorkoutSession(healthStore: store, configuration: configuration)
            let builder = session.associatedWorkoutBuilder()

            session.delegate = self
            builder.delegate = self
            
            print("WatchWorkoutManager: Setting up data source...")
            builder.dataSource = HKLiveWorkoutDataSource(healthStore: store, workoutConfiguration: configuration)

            self.session = session
            self.builder = builder

            print("WatchWorkoutManager: Preparing session...")
            session.prepare()
            
            let startDate = Date()
            print("WatchWorkoutManager: Starting activity at \(startDate)")
            session.startActivity(with: startDate)
            
            print("WatchWorkoutManager: Beginning collection...")
            do {
                try await builder.beginCollection(at: startDate)
                print("WatchWorkoutManager: Live collection started")
                startMotionSamplingIfPossible()
                sendSessionMarker("START", at: startDate)
                onWorkoutStateChange?("Workout running")
            } catch {
                print("WatchWorkoutManager: beginCollection failed: \(error.localizedDescription)")
                // Some watchOS versions might fail here if permissions aren't perfect
                onWorkoutStateChange?("Collection failed: \(error.localizedDescription)")
                throw error
            }

            print("WatchWorkoutManager: Session started successfully")
        } catch {
            print("WatchWorkoutManager: CRITICAL ERROR starting workout: \(error)")
            let errorMsg = (error as NSError).domain == "com.apple.healthkit" ? "HK Error \((error as NSError).code)" : error.localizedDescription
            onWorkoutStateChange?("Start failed: \(errorMsg)")
            cleanupAndReset()
            throw error
        }
    }

    func stopIfNeeded() async throws {
        guard let session else { return }
        guard !isStopping else { return }

        isStopping = true
        isFinalizing = false
        session.stopActivity(with: Date())

        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            stopContinuation = cont
        }
    }

    // MARK: - HKWorkoutSessionDelegate

    func workoutSession(_ workoutSession: HKWorkoutSession, didFailWithError error: Error) {
        print("Watch workoutSession failed: \(error)")
        completeStop(error: error)
        cleanupAndReset()
        onWorkoutStateChange?("Workout failed")
    }

    func workoutSession(_ workoutSession: HKWorkoutSession, didChangeTo toState: HKWorkoutSessionState, from fromState: HKWorkoutSessionState, date: Date) {
        if toState == .stopped || toState == .ended {
            finalizeStopFlow(endDate: date)
        }
    }

    // MARK: - HKLiveWorkoutBuilderDelegate

    func workoutBuilderDidCollectEvent(_ workoutBuilder: HKLiveWorkoutBuilder) {}

    func workoutBuilder(_ workoutBuilder: HKLiveWorkoutBuilder, didCollectDataOf collectedTypes: Set<HKSampleType>) {
        // Log some activity for debugging
        print("WatchWorkoutManager: Collected types update: \(collectedTypes.map { $0.identifier })")
        
        guard shouldSendVitalsNow() else { return }

        let mappings: [(HKQuantityTypeIdentifier, Int, HKUnit)] = [
            (.heartRate, 1, HKUnit.count().unitDivided(by: .minute())),
            (.heartRateVariabilitySDNN, 2, HKUnit.secondUnit(with: .milli)),
            (.stepCount, 20, .count()),
            (.distanceWalkingRunning, 21, .meter())
        ]

        var vitals: [[String: Any]] = []
        for (identifier, code, unit) in mappings {
            guard let quantityType = HKQuantityType.quantityType(forIdentifier: identifier) else { continue }
            
            var value: Double = 0
            if let stats = workoutBuilder.statistics(for: quantityType) {
                let quantity: HKQuantity?
                if identifier == .stepCount || identifier == .distanceWalkingRunning {
                    quantity = stats.sumQuantity()
                } else {
                    quantity = stats.mostRecentQuantity()
                }
                
                if let q = quantity {
                    value = q.doubleValue(for: unit)
                }
            }
            
            // For HR and HRV, we only send if we have a real non-zero sample
            if (identifier == .heartRate || identifier == .heartRateVariabilitySDNN) && value <= 0 {
                continue
            }
            // For steps and distance, we send 0 even if no data yet (per user request)
            
            vitals.append([
                "code": code,
                "val": value
            ])
            print("WatchWorkoutManager: \(identifier.rawValue) = \(value)")
        }

        vitals.append(contentsOf: consumeMotionFeatureItems())

        guard !vitals.isEmpty else { return }

        lastVitalsSentAt = Date()
        var payload: [String: Any] = [
            "type": "vitals",
            "device_id": WatchIdentity.deviceId,
            "t": ISO8601DateFormatter().string(from: Date()),
            "items": vitals
        ]
        if let currentSessionKey, !currentSessionKey.isEmpty {
            payload["session_key"] = currentSessionKey
        }
        onVitals?(payload)
    }

    // MARK: - Private

    private func requestAuthorizationIfNeeded() async throws {
        guard HKHealthStore.isHealthDataAvailable() else {
            print("WatchWorkoutManager: Health data is not available on this device")
            return
        }

        let toShare: Set = [HKObjectType.workoutType()]
        let toRead: Set = [
            HKQuantityType.quantityType(forIdentifier: .heartRate)!,
            HKQuantityType.quantityType(forIdentifier: .heartRateVariabilitySDNN)!,
            HKQuantityType.quantityType(forIdentifier: .stepCount)!,
            HKQuantityType.quantityType(forIdentifier: .distanceWalkingRunning)!
        ]

        print("WatchWorkoutManager: Requesting HealthKit authorization from Watch OS...")
        // Explicitly check current status before requesting (sometimes helps if it's already denied/allowed)
        let status = store.authorizationStatus(for: .workoutType())
        print("WatchWorkoutManager: Current workout auth status: \(status.rawValue)")
        
        do {
            try await store.requestAuthorization(toShare: toShare, read: toRead)
            print("WatchWorkoutManager: HealthKit authorization request completed")
        } catch {
            print("WatchWorkoutManager: HealthKit authorization FAILED with error: \(error)")
            // If it fails because of background execution or other UI reasons, we still proceed if we have a session
        }
    }

    private func shouldSendVitalsNow() -> Bool {
        let now = Date()
        if let last = lastVitalsSentAt, now.timeIntervalSince(last) < 1.0 {
            return false
        }
        return true
    }

    private func finalizeStopFlow(endDate: Date) {
        guard !isFinalizing else { return }
        isFinalizing = true
        sendSessionMarker("END", at: endDate)

        guard let builder = builder, let session = session else {
            completeStop(error: nil)
            cleanupAndReset()
            onWorkoutStateChange?("Workout stopped")
            return
        }

        builder.endCollection(withEnd: endDate) { [weak self] _, endError in
            guard let self else { return }
            if let endError {
                print("Watch endCollection error: \(endError)")
                self.completeStop(error: endError)
                self.cleanupAndReset()
                self.onWorkoutStateChange?("Workout stop failed")
                return
            }

            builder.finishWorkout { _, finishError in
                if let finishError {
                    print("Watch finishWorkout error: \(finishError)")
                }
                session.end()
                self.completeStop(error: finishError)
                self.cleanupAndReset()
                self.onWorkoutStateChange?("Workout stopped")
            }
        }
    }

    private func completeStop(error: Error?) {
        guard let cont = stopContinuation else {
            isStopping = false
            return
        }
        stopContinuation = nil
        isStopping = false

        if let error {
            cont.resume(throwing: error)
        } else {
            cont.resume()
        }
    }

    private func cleanupAndReset() {
        stopMotionSampling()
        session = nil
        builder = nil
        stopContinuation = nil
        isStarting = false
        isStopping = false
        isFinalizing = false
        lastVitalsSentAt = nil
        currentSessionKey = nil
        lastEmittedMotionContext = nil
    }

    private func startMotionSamplingIfPossible() {
        // High-frequency sensor stream (Accel + Gyro via DeviceMotion)
        // startDeviceMotion() // Disabled high-frequency accel/gyro data collection
        
        // Low-frequency context stream (Activity)
        startActivityUpdates()
    }
    
    private func startDeviceMotion() {
        guard motionManager.isDeviceMotionAvailable else {
            print("WatchWorkoutManager: Device Motion unavailable")
            return
        }
        if motionManager.isDeviceMotionActive { return }

        motionLock.lock()
        accelMagnitudeWindow.removeAll(keepingCapacity: true)
        gyroXWindow.removeAll(keepingCapacity: true)
        gyroYWindow.removeAll(keepingCapacity: true)
        gyroZWindow.removeAll(keepingCapacity: true)
        motionLock.unlock()

        motionManager.deviceMotionUpdateInterval = 1.0 / 50.0 // 50 Hz
        motionManager.startDeviceMotionUpdates(to: motionQueue) { [weak self] data, error in
            guard let self else { return }
            if let error {
                print("WatchWorkoutManager: DeviceMotion error: \(error)")
                return
            }
            guard let data else { return }

            // Reconstruct total acceleration vector ( Gravity + UserAccel ) to match raw accelerometer behavior
            let totalAccelX = data.gravity.x + data.userAcceleration.x
            let totalAccelY = data.gravity.y + data.userAcceleration.y
            let totalAccelZ = data.gravity.z + data.userAcceleration.z
            
            let m = sqrt(
                totalAccelX * totalAccelX +
                totalAccelY * totalAccelY +
                totalAccelZ * totalAccelZ
            )
            
            // Rotation Rate (Gyro) in rad/s
            let rx = data.rotationRate.x
            let ry = data.rotationRate.y
            let rz = data.rotationRate.z

            self.motionLock.lock()
            
            // Buffer Accel Magnitude
            self.accelMagnitudeWindow.append(m)
            if self.accelMagnitudeWindow.count > 300 { // Keep last ~6s @ 50hz
                self.accelMagnitudeWindow.removeFirst(self.accelMagnitudeWindow.count - 300)
            }
            
            // Buffer Gyro
            self.gyroXWindow.append(rx)
            if self.gyroXWindow.count > 300 { self.gyroXWindow.removeFirst(self.gyroXWindow.count - 300) }
            
            self.gyroYWindow.append(ry)
            if self.gyroYWindow.count > 300 { self.gyroYWindow.removeFirst(self.gyroYWindow.count - 300) }
            
            self.gyroZWindow.append(rz)
            if self.gyroZWindow.count > 300 { self.gyroZWindow.removeFirst(self.gyroZWindow.count - 300) }
            
            self.motionLock.unlock()
        }
    }
    
    private func startActivityUpdates() {
        guard CMMotionActivityManager.isActivityAvailable() else {
            print("WatchWorkoutManager: Activity unavailable")
            return 
        }
        
        // No "isActive" check on activityManager, safe to call start repeatedly (it just restarts)
        activityManager.startActivityUpdates(to: motionQueue) { [weak self] activity in
            guard let self, let activity else { return }
            
            // Map activity to integer code
            // 0: Unknown, 1: Stationary, 2: Walking, 3: Running, 4: Automotive, 5: Cycling
            var code = 0
            if activity.stationary { code = 1 }
            else if activity.walking { code = 2 }
            else if activity.running { code = 3 }
            else if activity.automotive { code = 4 }
            else if activity.cycling { code = 5 }
            else if activity.unknown { code = 0 }
            
            self.motionLock.lock()
            self.latestActivityCode = code
            self.motionLock.unlock()

            guard self.session != nil else { return }
            let context = self.motionContextLabel(for: code)
            guard context != self.lastEmittedMotionContext else { return }
            self.lastEmittedMotionContext = context

            var metadata: [String: Any] = ["source": "watch_motion"]
            if let sessionKey = self.currentSessionKey, !sessionKey.isEmpty {
                metadata["session_key"] = sessionKey
            }

            self.onEvent?([
                "type": "event",
                "device_id": WatchIdentity.deviceId,
                "t": ISO8601DateFormatter().string(from: Date()),
                "label": "motion_context",
                "val_text": context,
                "metadata": metadata
            ])
        }
    }

    private func stopMotionSampling() {
        if motionManager.isDeviceMotionActive {
            motionManager.stopDeviceMotionUpdates()
        }
        activityManager.stopActivityUpdates()
        
        motionLock.lock()
        accelMagnitudeWindow.removeAll(keepingCapacity: false)
        gyroXWindow.removeAll(keepingCapacity: false)
        gyroYWindow.removeAll(keepingCapacity: false)
        gyroZWindow.removeAll(keepingCapacity: false)
        latestActivityCode = 0
        motionLock.unlock()
    }

    private func consumeMotionFeatureItems() -> [[String: Any]] {
        motionLock.lock()
        
        // Snapshot Windows
        let activity = latestActivityCode // Current context
        
        motionLock.unlock()

        var features: [[String: Any]] = []
        
        // High frequency Accel/Gyro sensors disabled
        
        // 3. Activity Context
        // Avoid sending 0 (unknown) repeatedly if not useful? Maybe just always send latest state.
        features.append(["code": 45, "val": Double(activity)])

        return features
    }

    private func sendSessionMarker(_ marker: String, at date: Date) {
        var metadata: [String: Any] = ["source": "watch_workout"]
        if let currentSessionKey, !currentSessionKey.isEmpty {
            metadata["session_key"] = currentSessionKey
        }
        onEvent?([
            "type": "event",
            "device_id": WatchIdentity.deviceId,
            "t": ISO8601DateFormatter().string(from: date),
            "label": "session_marker",
            "val_text": marker,
            "metadata": metadata
        ])
    }

    private func motionContextLabel(for code: Int) -> String {
        switch code {
        case 1:
            return "stationary"
        case 2:
            return "walking"
        case 3:
            return "running"
        case 4:
            return "driving"
        case 5:
            return "cycling"
        default:
            return "unknown"
        }
    }
}

private enum WatchIdentity {
    private static let defaults = UserDefaults.standard
    private static let deviceIdKey = "lamp.identity.watch_device_id"

    static var deviceId: String {
        if let existing = defaults.string(forKey: deviceIdKey), !existing.isEmpty {
            return existing
        }
        let created = "watch-\(UUID().uuidString.lowercased())"
        defaults.set(created, forKey: deviceIdKey)
        return created
    }
}
