import Foundation
import CoreLocation

final class LocationManager: NSObject, ObservableObject {
    static let shared = LocationManager()
    private enum SignalQuality: String {
        case precise
        case indoorFallback
        case unreliable
    }

    private let manager = CLLocationManager()
    private let sessionLock = DispatchQueue(label: "location.session.lock")
    private var sessionStartDate: Date?
    private var sessionEndDate: Date?
    var onLocationBatchItem: ((BatchItem) -> Void)?

    override private init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        manager.distanceFilter = kCLDistanceFilterNone
        manager.allowsBackgroundLocationUpdates = true
        manager.pausesLocationUpdatesAutomatically = true
    }

    func start() {
        manager.requestAlwaysAuthorization()
        manager.startUpdatingLocation()
        manager.startMonitoringSignificantLocationChanges()
    }

    func beginSession(at date: Date) {
        sessionLock.sync {
            sessionStartDate = date
            sessionEndDate = nil
        }
    }

    func endSession(at date: Date) {
        sessionLock.sync {
            sessionEndDate = date
        }
    }

    private func shouldCaptureSample(at date: Date) -> Bool {
        sessionLock.sync {
            guard let start = sessionStartDate else { return false }
            if let end = sessionEndDate {
                return date >= start && date <= end
            }
            return date >= start
        }
    }

    private func signalQuality(for location: CLLocation) -> SignalQuality {
        let horizontalAccuracy = location.horizontalAccuracy
        guard horizontalAccuracy >= 0 else { return .unreliable }
        if horizontalAccuracy <= 20 {
            return .precise
        }
        if horizontalAccuracy <= 65 {
            return .indoorFallback
        }
        return .unreliable
    }

    private func metadata(for location: CLLocation, signalQuality: SignalQuality) -> [String: JSONValue] {
        [
            "horizontalAccuracy": .number(location.horizontalAccuracy),
            "verticalAccuracy": location.verticalAccuracy >= 0 ? .number(location.verticalAccuracy) : .null,
            "is_low_power_mode": .bool(ProcessInfo.processInfo.isLowPowerModeEnabled),
            "is_indoor_likely": .bool(signalQuality == .indoorFallback),
            "signal_quality": .string(signalQuality.rawValue)
        ]
    }
}

extension LocationManager: CLLocationManagerDelegate {
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        Task {
            for loc in locations {
                guard shouldCaptureSample(at: loc.timestamp) else { continue }
                let signalQuality = signalQuality(for: loc)
                guard signalQuality != .unreliable else { continue }
                let context = MotionManager.shared.currentContext.rawValue
                let item = BatchItem(
                    type: .gps,
                    t: loc.timestamp,
                    lat: loc.coordinate.latitude,
                    lon: loc.coordinate.longitude,
                    acc: loc.horizontalAccuracy,
                    motion_context: context,
                    metadata: StudySessionContext.stamp(metadata: metadata(for: loc, signalQuality: signalQuality))
                )
                try? LocalStore.shared.append(item)
                onLocationBatchItem?(item)
            }
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if manager.authorizationStatus == .authorizedAlways || manager.authorizationStatus == .authorizedWhenInUse {
            manager.startUpdatingLocation()
            manager.startMonitoringSignificantLocationChanges()
        }
    }
}
