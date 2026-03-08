import SwiftUI

struct ContentView: View {
    static let hktFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .none
        f.timeStyle = .short
        f.timeZone = TimeZone(identifier: "Asia/Hong_Kong") ?? TimeZone(secondsFromGMT: 8 * 3600)
        return f
    }()

    fileprivate static let sensorDefinitions: [SensorDisplayType] = [
        .init(id: "vital_1", title: "Heart Rate", description: "Beats per minute from the live workout", kind: .vital(1), source: .watch),
        .init(id: "vital_2", title: "HRV (SDNN)", description: "Heart rate variability from the watch", kind: .vital(2), source: .watch),
        .init(id: "vital_45", title: "Motion Context", description: "Motion classifier from Apple Watch", kind: .vital(45), source: .watch),
        .init(id: "vital_20", title: "Steps", description: "Step counts from watch workout", kind: .vital(20), source: .watch),
        .init(id: "vital_21", title: "Distance", description: "Distance walked or run", kind: .vital(21), source: .watch),
        .init(id: "vital_40", title: "Accel Mean", description: "Mean accel magnitude window", kind: .vital(40), source: .watch),
        .init(id: "vital_41", title: "Accel StdDev", description: "Accel variability", kind: .vital(41), source: .watch),
        .init(id: "vital_42", title: "Gyro X Mean", description: "Rotation rate X axis", kind: .vital(42), source: .watch),
        .init(id: "vital_43", title: "Gyro Y Mean", description: "Rotation rate Y axis", kind: .vital(43), source: .watch),
        .init(id: "vital_44", title: "Gyro Z Mean", description: "Rotation rate Z axis", kind: .vital(44), source: .watch),
        .init(id: "gps", title: "GPS", description: "Latitude / longitude traces", kind: .gps, source: .phone),
        .init(id: "event_motion", title: "Motion Context", description: "Motion context from the iPhone", kind: .event("motion_context"), source: .phone),
        .init(id: "event_audio", title: "Audio Context", description: "SoundAnalysis AI scenes", kind: .event("audio_context"), source: .phone),
        .init(id: "vital_10", title: "Audio Exposure", description: "HealthKit audio exposure (dBA)", kind: .vital(10), source: .phone)
    ]

    @EnvironmentObject var scheduler: BatchScheduler
    @StateObject private var watchBridge = PhoneWatchBridge.shared
    @State private var sensorStats: [String: SensorStat] = [:]
    @State private var totalSamples: Int = 0
    @State private var sessionDurationMinutes: Double = 0
    @State private var latestItems: [BatchItem] = []

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("Watch Connectivity")) {
                    HStack {
                        Text("Connection")
                        Spacer()
                        Text(watchBridge.connectivityText)
                            .foregroundColor(color(for: watchBridge.connectivityColorName))
                    }
                    HStack {
                        Text("Workout State")
                        Spacer()
                        Text(scheduler.watchWorkoutState)
                            .foregroundColor(.secondary)
                    }
                }

                Section(header: Text("Sensor Sources")) {
                    SensorSourceCard(
                        title: "iPhone Sensors",
                        subtitle: scheduler.isSessionActive ? "Streaming ambient audio, GPS, motion" : "Idle until the next session",
                        iconName: "iphone.gen3",
                        isActive: scheduler.isSessionActive
                    )
                    SensorSourceCard(
                        title: "Apple Watch",
                        subtitle: watchBridge.connectivityText,
                        iconName: "applewatch",
                        isActive: watchBridge.isReachable
                    )
                }

                Section(header: Text("Session Management")) {
                    if !scheduler.isSessionActive {
                        Button(action: { scheduler.startStudySession() }) {
                            HStack {
                                Image(systemName: "play.circle.fill")
                                Text("Start Study Session")
                                    .bold()
                            }
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(Color.green)
                            .foregroundColor(.white)
                            .cornerRadius(10)
                        }
                        Text("Starts a clean tracking session. Discards all previous ambient data.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    } else {
                        VStack(spacing: 12) {
                            HStack {
                                Circle()
                                    .fill(Color.red)
                                    .frame(width: 8, height: 8)
                                Text("Sensing Active")
                                    .font(.subheadline.bold())
                                    .foregroundColor(.red)
                            }

                            Button(action: { scheduler.endStudySession() }) {
                                HStack {
                                    Image(systemName: "stop.circle.fill")
                                    Text("End Session & Upload")
                                        .bold()
                                }
                                .frame(maxWidth: .infinity)
                                .padding()
                                .background(Color.red)
                                .foregroundColor(.white)
                                .cornerRadius(10)
                            }
                        }
                    }
                }

                Section(header: Text(scheduler.isSessionActive ? "Current Session" : "Previous Session")) {
                    if scheduler.isSessionActive {
                        if let start = scheduler.currentSessionStartTime {
                            Text("Started: \(Self.hktFormatter.string(from: start))")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    } else {
                        if let start = scheduler.previousSessionStartTime {
                            Text("Started: \(Self.hktFormatter.string(from: start))")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        if let end = scheduler.previousSessionEndTime {
                            Text("Ended: \(Self.hktFormatter.string(from: end))")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    HStack {
                        Text("Total Samples")
                        Spacer()
                        Text("\(totalSamples)")
                            .bold()
                    }
                    HStack {
                        Text("Duration")
                        Spacer()
                        Text(String(format: "%.1f min", sessionDurationMinutes))
                            .foregroundColor(.secondary)
                    }
                    HStack {
                        Text("Overall Frequency")
                        Spacer()
                        let freq = sessionDurationMinutes > 0 ? Double(totalSamples) / sessionDurationMinutes : 0
                        Text(String(format: "%.2f/min", freq))
                            .foregroundColor(.secondary)
                    }
                }

                ForEach(SensorDisplayType.SourceCategory.allCases, id: \.self) { source in
                    let definitions = definitions(for: source)
                    if !definitions.isEmpty {
                        Section(header: Text(source.displayTitle)) {
                            ForEach(definitions) { definition in
                                NavigationLink(destination: DataTypeDetailView(title: definition.title, items: items(for: definition))) {
                                    SensorStatRow(
                                        title: definition.title,
                                        description: definition.description,
                                        count: sensorStats[definition.id]?.count ?? 0,
                                        frequency: sensorStats[definition.id]?.frequencyPerMinute ?? 0
                                    )
                                }
                            }
                        }
                    }
                }
            }
            .listStyle(InsetGroupedListStyle())
            .navigationTitle("Live Sensing")
            .onAppear { refreshInitialStats() }
            .onReceive(scheduler.$runningSessionItems) { items in
                if scheduler.isSessionActive {
                    updateStats(with: items, start: scheduler.currentSessionStartTime, end: nil)
                }
            }
            .onReceive(scheduler.$previousSessionItems) { items in
                if !scheduler.isSessionActive {
                    updateStats(with: items, start: scheduler.previousSessionStartTime, end: scheduler.previousSessionEndTime)
                }
            }
        }
    }

    private func refreshInitialStats() {
        if scheduler.isSessionActive {
            updateStats(with: scheduler.runningSessionItems, start: scheduler.currentSessionStartTime, end: nil)
        } else {
            updateStats(with: scheduler.previousSessionItems, start: scheduler.previousSessionStartTime, end: scheduler.previousSessionEndTime)
        }
    }

    private func updateStats(with items: [BatchItem], start: Date?, end: Date?) {
        latestItems = items
        let durationSeconds: TimeInterval
        if let start {
            let endDate = end ?? Date()
            durationSeconds = max(endDate.timeIntervalSince(start), 1)
        } else if let first = items.first?.t, let last = items.last?.t {
            durationSeconds = max(last.timeIntervalSince(first), 1)
        } else {
            durationSeconds = 60
        }
        sessionDurationMinutes = durationSeconds / 60
        totalSamples = items.count

        var stats: [String: SensorStat] = [:]
        for definition in Self.sensorDefinitions {
            let count = countItems(for: definition, in: items)
            let frequency = sessionDurationMinutes > 0 ? Double(count) / sessionDurationMinutes : 0
            stats[definition.id] = SensorStat(count: count, frequencyPerMinute: frequency)
        }
        sensorStats = stats
    }

    private func countItems(for definition: SensorDisplayType, in items: [BatchItem]) -> Int {
        switch definition.kind {
        case .vital(let code):
            return items.filter { $0.type == .vital && $0.code == code }.count
        case .event(let label):
            return items.filter { $0.type == .event && $0.label == label }.count
        case .gps:
            return items.filter { $0.type == .gps }.count
        }
    }

    private func definitions(for source: SensorDisplayType.SourceCategory) -> [SensorDisplayType] {
        Self.sensorDefinitions.filter { $0.source == source }
    }

    private func items(for definition: SensorDisplayType) -> [BatchItem] {
        switch definition.kind {
        case .vital(let code):
            return latestItems.filter { $0.type == .vital && $0.code == code }
                .sorted { $0.t > $1.t }
        case .event(let label):
            return latestItems.filter { $0.type == .event && $0.label == label }
                .sorted { $0.t > $1.t }
        case .gps:
            return latestItems.filter { $0.type == .gps }
                .sorted { $0.t > $1.t }
        }
    }

    private func color(for name: String) -> Color {
        switch name {
        case "green":
            return .green
        case "orange":
            return .orange
        default:
            return .red
        }
    }
}

private struct SensorStatRow: View {
    let title: String
    let description: String
    let count: Int
    let frequency: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                    .font(.headline)
                Spacer()
                Text("\(count)")
                    .bold()
            }
            HStack {
                Text(description)
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Text(String(format: "%.2f/min", frequency))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

private struct SensorSourceCard: View {
    let title: String
    let subtitle: String
    let iconName: String
    let isActive: Bool

    var body: some View {
        HStack(spacing: 16) {
            Image(systemName: iconName)
                .font(.system(size: 28))
                .foregroundColor(.white)
                .padding(12)
                .background(isActive ? Color.blue : Color.gray)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.headline)
                Text(subtitle)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
            Circle()
                .fill(isActive ? Color.green : Color.gray)
                .frame(width: 12, height: 12)
        }
        .padding(.vertical, 4)
    }
}

private struct SensorStat {
    let count: Int
    let frequencyPerMinute: Double
}

private struct SensorDisplayType: Identifiable {
    enum Kind {
        case vital(Int)
        case event(String)
        case gps
    }

    enum SourceCategory: CaseIterable {
        case phone
        case watch
        case combined

        var displayTitle: String {
            switch self {
            case .phone: return "iPhone Data Types"
            case .watch: return "Apple Watch Data Types"
            case .combined: return "Shared Data Types"
            }
        }
    }

    let id: String
    let title: String
    let description: String
    let kind: Kind
    let source: SourceCategory
}

struct DataTypeDetailView: View {
    let title: String
    let items: [BatchItem]

    var body: some View {
        List {
            if items.isEmpty {
                Text("No samples captured for this data type during the selected session.")
                    .foregroundColor(.secondary)
                    .padding()
            } else {
                ForEach(items.indices, id: \.self) { idx in
                    let sample = items[idx]
                    VStack(alignment: .leading, spacing: 4) {
                        Text(sampleTitle(for: sample))
                            .bold()
                        if let detail = sampleDetail(for: sample) {
                            Text(detail)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        Text(ContentView.hktFormatter.string(from: sample.t))
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .navigationTitle(title)
    }

    private func sampleTitle(for item: BatchItem) -> String {
        switch item.type {
        case .event:
            let label = item.label ?? "Event"
            var valueText = item.val_text ?? ""
            
            // Format Motion Context value
            if label == "motion_context", let vt = item.val_text {
                // If it's a number (watch data mapped to number), use the helper
                if let code = Int(vt) {
                    valueText = motionStateName(for: code)
                } else {
                    // It's already a string (phone data), just capitalize it
                    valueText = vt.capitalized
                }
            }
            
            if !valueText.isEmpty {
                return "\(label) — \(valueText)"
            }
            return label
        case .vital:
            let code = item.code ?? 0
            let name = ContentView.sensorDefinitions.first(where: { definition in
                if case .vital(let defCode) = definition.kind { return defCode == code }
                return false
            })?.title ?? "Metric \(code)"
            if let value = item.val {
                if code == 45 {
                    return "\(name): \(motionStateName(for: Int(value.rounded())))"
                }
                return "\(name): \(Int(value.rounded()))"
            }
            return name
        case .gps:
            return "GPS Reading"
        }
    }

    private func motionStateName(for code: Int) -> String {
        switch code {
        case 0: return "Unknown"
        case 1: return "Stationary"
        case 2: return "Walking"
        case 3: return "Running"
        case 4: return "Automotive"
        case 5: return "Cycling"
        default: return "Other (\(code))"
        }
    }

    private func sampleDetail(for item: BatchItem) -> String? {
        switch item.type {
        case .gps:
            if let lat = item.lat, let lon = item.lon {
                return String(format: "Lat %.4f, Lon %.4f", lat, lon)
            }
            return item.motion_context?.capitalized
        case .event:
            if let metadata = item.metadata, !metadata.isEmpty {
                return metadata
                    .sorted { $0.key < $1.key }
                    .map { "\($0.key)=\($0.value.displayText)" }
                    .joined(separator: ", ")
            }
            return nil
        case .vital:
            if let context = item.motion_context {
                return context.capitalized
            }
            return nil
        }
    }
}
