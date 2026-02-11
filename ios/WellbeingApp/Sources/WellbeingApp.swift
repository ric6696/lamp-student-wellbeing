import SwiftUI

@main
struct WellbeingApp: App {
    @StateObject private var scheduler = BatchScheduler(intervalMinutes: 15)

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
                .task { await scheduler.flushIfNeeded(reason: .appOpen) }
        }
    }
}
