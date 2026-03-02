import Foundation
import CoreLocation

final class LocationManager: NSObject, ObservableObject {
    static let shared = LocationManager()
    private let manager = CLLocationManager()
    private let sessionLock = DispatchQueue(label: "location.session.lock")
    private var sessionStartDate: Date?
    private var sessionEndDate: Date?

    override private init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = 10
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
}

extension LocationManager: CLLocationManagerDelegate {
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        Task {
            for loc in locations {
                guard shouldCaptureSample(at: loc.timestamp) else { continue }
                let context = MotionManager.shared.currentContext.rawValue
                let item = BatchItem(
                    type: .gps,
                    t: loc.timestamp,
                    lat: loc.coordinate.latitude,
                    lon: loc.coordinate.longitude,
                    acc: loc.horizontalAccuracy,
                    motion_context: context
                )
                try? LocalStore.shared.append(item)
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
