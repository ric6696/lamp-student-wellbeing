import SwiftUI

struct ContentView: View {
    @EnvironmentObject var scheduler: BatchScheduler
    @State private var status: String = "Idle"

    var body: some View {
        VStack(spacing: 16) {
            Text("Sync status: \(status)")
            Button("Flush now") {
                Task {
                    status = "Flushing..."
                    let ok = await scheduler.flushIfNeeded(reason: .manual)
                    status = ok ? "Flushed" : "Failed"
                }
            }

            Button("Clear Buffer") {
                try? LocalStore.shared.clear()
                status = "Buffer cleared"
            }
            .foregroundColor(.red)
            .padding(.top, 10)
        }
        .padding()
    }
}
