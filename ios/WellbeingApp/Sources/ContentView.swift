import SwiftUI

struct ContentView: View {
    static let hktFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .none
        f.timeStyle = .short
        f.timeZone = TimeZone(identifier: "Asia/Hong_Kong") ?? TimeZone(secondsFromGMT: 8 * 3600)
        return f
    }()
    static let metricNames: [Int: String] = [
        1: "Heart Rate",
        2: "HRV (SDNN)",
        3: "Resting HR",
        5: "Active Energy",
        10: "Audio Exposure",
        20: "Steps",
        21: "Distance",
        30: "Respiratory Rate"
    ]
    static let metricOrder: [Int] = [1, 2, 3, 5, 20, 21, 30]
    @EnvironmentObject var scheduler: BatchScheduler
    @State private var dataSummaries: [DataTypeSummary] = []
    @State private var liveMetrics: [MetricSummary] = []

    var body: some View {
        NavigationView {
            List {
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

                Section(header: Text("Live Session Metrics")) {
                    if liveMetrics.isEmpty {
                        Text("No-quality data yet. Start a session to prime all trackers.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    } else {
                        ForEach(liveMetrics) { summary in
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(summary.title)
                                        .font(.subheadline)
                                    if let detail = summary.detail {
                                        Text(detail)
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }
                                }
                                Spacer()
                                Text("\(summary.counted)")
                                    .foregroundColor(.secondary)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }

                Section(header: Text(scheduler.isSessionActive ? "Current Session" : "Previous Session")) {
                    if dataSummaries.isEmpty {
                        Text("No samples yet. Start a session to see live readings.")
                            .foregroundColor(.secondary)
                    } else {
                        ForEach(dataSummaries) { summary in
                            NavigationLink(destination: DataTypeDetailView(title: summary.title, items: summary.items)) {
                                HStack {
                                    Text(summary.title)
                                    Spacer()
                                    Text("\(summary.items.count)")
                                        .foregroundColor(.secondary)
                                }
                            }
                        }
                    }
                }
            }
            .listStyle(InsetGroupedListStyle())
            .navigationTitle("Live Sensing")
            .onReceive(scheduler.$runningSessionItems) { items in
                if scheduler.isSessionActive {
                    updateSummaries(with: items)
                }
            }
            .onReceive(scheduler.$previousSessionItems) { items in
                if !scheduler.isSessionActive {
                    updateSummaries(with: items)
                }
            }
            .onReceive(scheduler.$sessionMetricsSnapshot) { snapshot in
                refreshLiveMetrics(with: snapshot)
            }
        }
    }

    private func updateSummaries(with items: [BatchItem]) {
        let grouped = Dictionary(grouping: items, by: dataTypeTitle(for:))
        let summaries = grouped.map { DataTypeSummary(title: $0.key, items: $0.value.reversed()) }
            .sorted { $0.items.count > $1.items.count }
        dataSummaries = summaries
    }

    private func refreshLiveMetrics(with snapshot: SessionMetricsSnapshot) {
        var summaries: [MetricSummary] = []
        for code in Self.metricOrder {
            guard let item = snapshot.vitals[code], let value = item.val else { continue }
            let title = Self.metricNames[code] ?? "Metric \(code)"
            let detail = "Latest: \(Int(value.rounded()))"
            let count = snapshot.counts["vital_\(code)"] ?? 0
            summaries.append(MetricSummary(title: title, detail: detail, counted: count))
        }
        if let audio = snapshot.latestEnvironmentalAudio, let value = audio.val {
            let count = snapshot.counts["vital_environmental_audio"] ?? 0
            summaries.append(MetricSummary(title: "Environmental Audio", detail: "Latest: \(Int(value.rounded()))", counted: count))
        }
        if let ambient = snapshot.latestAmbientNoise, let value = ambient.val {
            let count = snapshot.counts["vital_ambient_noise"] ?? 0
            summaries.append(MetricSummary(title: "Ambient Noise", detail: "Latest: \(Int(value.rounded()))", counted: count))
        }
        if let motionEvent = snapshot.events["motion_context"], let val = motionEvent.val_text {
            let count = snapshot.counts["event_motion_context"] ?? 0
            summaries.append(MetricSummary(title: "Motion Context", detail: val.capitalized, counted: count))
        }
        if let sessionEvent = snapshot.events["session_marker"], let val = sessionEvent.val_text {
            let count = snapshot.counts["event_session_marker"] ?? 0
            summaries.append(MetricSummary(title: "Session Marker", detail: val.capitalized, counted: count))
        }
        liveMetrics = summaries
    }

    private func dataTypeTitle(for item: BatchItem) -> String {
        switch item.type {
        case .event:
            return item.label ?? "Event"
        case .vital:
            return Self.metricNames[item.code ?? 0] ?? "Metric \(item.code ?? 0)"
        case .gps:
            return "GPS"
        }
    }
}

struct MetricSummary: Identifiable {
    let id = UUID()
    let title: String
    let detail: String?
    let counted: Int
}

struct DataTypeSummary: Identifiable {
    let id = UUID()
    let title: String
    let items: [BatchItem]
}

struct DataTypeDetailView: View {
    let title: String
    let items: [BatchItem]

    var body: some View {
        List {
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
        .navigationTitle(title)
    }

    private func sampleTitle(for item: BatchItem) -> String {
        switch item.type {
        case .event:
            if let val = item.val_text, !val.isEmpty {
                return "\(item.label ?? "Event") — \(val)"
            }
            return item.label ?? "Event"
        case .vital:
            let name = ContentView.metricNames[item.code ?? 0] ?? "Metric \(item.code ?? 0)"
            if let value = item.val {
                return "\(name): \(Int(value.rounded()))"
            }
            return name
        case .gps:
            return "GPS Reading"
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
                return metadata.map { "\($0.key)=\($0.value)" }.joined(separator: ", ")
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
