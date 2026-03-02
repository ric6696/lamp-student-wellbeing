import SwiftUI

@main
struct WellbeingWatchApp: App {
    @State private var statusText: String = "Idle"

    var body: some Scene {
        WindowGroup {
            VStack(spacing: 8) {
                Text("Wellbeing")
                    .font(.headline)
                Text(statusText)
                    .font(.caption)
                    .multilineTextAlignment(.center)
            }
            .padding()
            .onAppear {
                WatchPhoneBridge.shared.onStatusUpdate = { status in
                    Task { @MainActor in
                        statusText = status
                    }
                }
                WatchPhoneBridge.shared.start()
            }
        }
    }
}
