import SwiftUI

@main
struct WellbeingApp: App {
    // Set the flush interval to 10 seconds so the "Data Retrieved" counts refresh frequently during a study session.
    @StateObject private var scheduler = BatchScheduler(intervalMinutes: 10.0 / 60.0)

    init() {
        Task { await HealthKitManager.shared.requestAuthorization() }
        LocationManager.shared.start()
        MotionManager.shared.start()
        NoiseManager.shared.start()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(scheduler)
                .onAppear { scheduler.resume() }
        }
    }
}
