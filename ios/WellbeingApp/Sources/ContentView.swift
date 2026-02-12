import SwiftUI

struct ContentView: View {
    static let hktFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .none
        f.timeStyle = .short
        f.timeZone = TimeZone(identifier: "Asia/Hong_Kong") ?? TimeZone(secondsFromGMT: 8 * 3600)
        return f
    }()
    @EnvironmentObject var scheduler: BatchScheduler
    @State private var status: String = "Idle"
    @State private var lastSync: Date?
    @State private var localCount: Int = 0

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("Sensing Control")) {
                    Toggle(isOn: $scheduler.isEnabled) {
                        HStack {
                            Image(systemName: scheduler.isEnabled ? "antenna.radiowaves.left.and.right" : "antenna.radiowaves.left.and.right.slash")
                                .foregroundColor(scheduler.isEnabled ? .green : .gray)
                            VStack(alignment: .leading) {
                                Text("Active Sensing")
                                    .font(.headline)
                                Text(scheduler.isEnabled ? "Tracking & Uploading" : "Paused")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                }

                Section(header: Text("Data Sync")) {
                    HStack {
                        Text("Status")
                        Spacer()
                        Text(status)
                            .foregroundColor(.secondary)
                    }
                    
                    HStack {
                        Text("Local Buffer")
                        Spacer()
                        Text("\(localCount) items")
                            .foregroundColor(.secondary)
                    }

                    if let last = lastSync {
                        HStack {
                            Text("Last Sync (HKT)")
                            Spacer()
                            Text(Self.hktFormatter.string(from: last))
                                .foregroundColor(.secondary)
                        }
                    }

                    Button(action: { triggerFlush() }) {
                        HStack {
                            Spacer()
                            Image(systemName: "arrow.clockwise.circle.fill")
                            Text("Flush Data Now")
                            Spacer()
                        }
                    }
                    .disabled(status == "Flushing...")
                }

                Section(header: Text("Advanced")) {
                    Button(action: { 
                        try? LocalStore.shared.clear()
                        updateStats()
                        status = "Buffer cleared"
                    }) {
                        Text("Clear Local Buffer")
                            .foregroundColor(.red)
                    }
                }
            }
            .listStyle(InsetGroupedListStyle())
            .navigationTitle("Wellbeing Tracker")
            .onAppear { updateStats() }
        }
    }

    private func triggerFlush() {
        Task {
            status = "Flushing..."
            let ok = await scheduler.flushIfNeeded(reason: .manual)
            status = ok ? "Success" : "Failed"
            if ok { lastSync = Date() }
            updateStats()
        }
    }

    private func updateStats() {
        localCount = (try? LocalStore.shared.count()) ?? 0
    }
}
