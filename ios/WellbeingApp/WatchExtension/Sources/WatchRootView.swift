import SwiftUI

struct WatchRootView: View {
    @State private var statusText: String = "Idle"

    var body: some View {
        VStack(spacing: 8) {
            Text("Wellbeing")
                .font(.headline)
            Text(statusText)
                .font(.caption)
                .multilineTextAlignment(.center)
        }
        .padding()
        .onAppear {
            WatchCompanionController.shared.onStatusUpdate = { status in
                Task { @MainActor in
                    statusText = status
                }
            }
            WatchCompanionController.shared.start()
        }
    }
}
