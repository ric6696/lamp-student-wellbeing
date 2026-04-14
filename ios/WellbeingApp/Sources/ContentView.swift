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
        .init(id: "vital_45", title: "Motion Context", description: "Motion classifier from Apple Watch", kind: .vital(45), source: .watch),
        .init(id: "vital_20", title: "Steps", description: "Step counts from watch workout", kind: .vital(20), source: .watch),
        .init(id: "vital_21", title: "Distance", description: "Distance walked or run", kind: .vital(21), source: .watch),
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
    
    @State private var activityContext: String = ""
    @State private var environmentContext: String = ""
    @State private var mentalContext: String = ""

    @State private var prevActivityContext: String = ""
    @State private var prevEnvironmentContext: String = ""
    @State private var prevMentalContext: String = ""

    @State private var showPostSessionSheet: Bool = false
    @State private var prevConcentration: Int? = nil
    @State private var prevDistractions: [String]? = nil

    private let activities = ["Study", "Lecture", "Group Meeting", "Reading", "Writing / Report Work"]
    private let environments = ["Library", "Classroom", "Cafe", "Home", "Outdoor"]
    private let mentalStates = ["Very Low", "Low", "Neutral", "High", "Very High"]
    private let distractionOptions = [
        "Environmental Noise / Speech",
        "Movement / Restlessness",
        "Location Change / Transition",
        "Physiological Strain (stress, fatigue, discomfort)",
        "Internal Cognitive Drift (mind wandering, low motivation)",
        "Task Challenge (difficulty, frustration)",
        "No Major Distraction",
        "Others"
    ]
    private let watchSteps = ["DOWNLOADED", "REACHABLE", "CONNECTED"]

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("Watch Data Collection"), footer: Text("Enable to collect heart rate, motion, and workout data from your Apple Watch")) {
                    HStack {
                        Text("Collect Watch Data")
                        Spacer()
                        Toggle("", isOn: $watchBridge.enableWatchDataCollection)
                            .labelsHidden()
                    }
                }
                
                if watchBridge.enableWatchDataCollection {
                    Section {
                        watchConnectionStepsView
                    }
                }

                Section(header: Text("Pre-Session Context")) {
                    HStack {
                        Image(systemName: activityContext.isEmpty ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                            .foregroundColor(activityContext.isEmpty ? .orange : .green)
                        Picker("Activity", selection: $activityContext) {
                            Text("Select...").tag("")
                            ForEach(activities, id: \.self) { Text($0).tag($0) }
                        }
                    }
                    HStack {
                        Image(systemName: environmentContext.isEmpty ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                            .foregroundColor(environmentContext.isEmpty ? .orange : .green)
                        Picker("Environment", selection: $environmentContext) {
                            Text("Select...").tag("")
                            ForEach(environments, id: \.self) { Text($0).tag($0) }
                        }
                    }
                    HStack {
                        Image(systemName: mentalContext.isEmpty ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                            .foregroundColor(mentalContext.isEmpty ? .orange : .green)
                        Picker("Mental Preparedness", selection: $mentalContext) {
                            Text("Select...").tag("")
                            ForEach(mentalStates, id: \.self) { Text($0).tag($0) }
                        }
                    }
                }
                .disabled(scheduler.isSessionActive)

                Section(header: Text("Session Management")) {
                    if !scheduler.isSessionActive {
                        let isWatchReady = !watchBridge.enableWatchDataCollection || watchBridge.isReachable
                        let isQuestionnaireComplete = !activityContext.isEmpty && !environmentContext.isEmpty && !mentalContext.isEmpty
                        let canStart = isWatchReady && isQuestionnaireComplete
                        
                        Button(action: {
                            if canStart {
                                scheduler.startStudySession()
                            }
                        }) {
                            HStack {
                                Image(systemName: canStart ? "play.circle.fill" : "lock.fill")
                                Text(canStart ? "Start Study Session" : (!isWatchReady ? "Watch Not Reachable" : "Answer Questions"))
                                    .bold()
                            }
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(canStart ? Color.green : (!isWatchReady ? Color.gray : Color.orange))
                            .foregroundColor(.white)
                            .cornerRadius(10)
                        }
                        .disabled(!canStart)
                        
                        if !isWatchReady {
                            Text("Connect to your Apple Watch to establish a connection before starting the session.")
                                .font(.caption)
                                .foregroundColor(.orange)
                        } else if !isQuestionnaireComplete {
                            Text("Please complete the pre-session questions above before starting.")
                                .font(.caption)
                                .foregroundColor(.orange)
                        } else {
                            Text("Starts a clean tracking session. Discards all previous ambient data.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
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

                            Button(action: {
                                prevActivityContext = activityContext
                                prevEnvironmentContext = environmentContext
                                prevMentalContext = mentalContext
                                scheduler.endStudySession(
                                    activityContext: activityContext,
                                    environmentContext: environmentContext,
                                    mentalContext: mentalContext
                                )
                                activityContext = ""
                                environmentContext = ""
                                mentalContext = ""
                                showPostSessionSheet = true
                            }) {
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

                Section(header: Text("Sensor Sources")) {
                    SensorSourceCard(
                        title: "iPhone",
                        subtitle: scheduler.isSessionActive ? "Streaming data" : "Idle until the next session",
                        iconName: "iphone.gen3",
                        isActive: scheduler.isSessionActive
                    )
                    if watchBridge.enableWatchDataCollection {
                        SensorSourceCard(
                            title: "Apple Watch",
                            subtitle: watchBridge.connectivityText,
                            iconName: "applewatch",
                            isActive: scheduler.isSessionActive && watchBridge.isReachable
                        )
                    }
                }

                Section(header: Text(scheduler.isSessionActive ? "Current Session" : "Previous Session")) {
                    if scheduler.isSessionActive {
                        if let start = scheduler.currentSessionStartTime {
                            Text("Started: \(Self.hktFormatter.string(from: start))")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        if !activityContext.isEmpty {
                            Text("\(activityContext) • \(environmentContext) • \(mentalContext)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    } else {
                        if let start = scheduler.previousSessionStartTime {
                            let startStr = Self.hktFormatter.string(from: start)
                            let endStr = scheduler.previousSessionEndTime != nil ? Self.hktFormatter.string(from: scheduler.previousSessionEndTime!) : "?"
                            Text("Started: \(startStr)   Ended: \(endStr)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        if !prevActivityContext.isEmpty {
                            Text("\(prevActivityContext) • \(prevEnvironmentContext) • \(prevMentalContext)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        if let conc = prevConcentration, let dist = prevDistractions, !dist.isEmpty {
                            Text("Concentration: \(conc)/10 • Distracted by: \(dist.joined(separator: ", "))")
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
            .sheet(isPresented: $showPostSessionSheet) {
                PostSessionEvaluationView(isPresented: $showPostSessionSheet, distractions: distractionOptions) { rating, distractions in
                    prevConcentration = rating
                    prevDistractions = distractions
                    saveUserResponseToConcentration(rating: rating, distractions: distractions)
                }
                .interactiveDismissDisabled(true)
            }
        }
    }

    private var watchThemeColor: Color {
        watchBridge.currentConnectionStep >= 2 ? .green : .orange
    }

    private var watchConnectionStepsView: some View {
        VStack(spacing: 16) {
            Text("Watch Connection Progress")
                .font(.headline)
                .frame(maxWidth: .infinity, alignment: .leading)

            HStack(spacing: 0) {
                ForEach(0..<watchSteps.count, id: \.self) { index in
                    VStack(spacing: 10) {
                        ZStack {
                            HStack(spacing: 0) {
                                Rectangle()
                                    .fill(index == 0 ? Color.clear : (index <= watchBridge.currentConnectionStep ? watchThemeColor : Color.gray.opacity(0.3)))
                                    .frame(height: 2)
                                Rectangle()
                                    .fill(index == watchSteps.count - 1 ? Color.clear : (index < watchBridge.currentConnectionStep ? watchThemeColor : Color.gray.opacity(0.3)))
                                    .frame(height: 2)
                            }

                            ZStack {
                                if index < watchBridge.currentConnectionStep {
                                    Circle()
                                        .fill(watchThemeColor)
                                        .frame(width: 24, height: 24)
                                    Image(systemName: "checkmark")
                                        .font(.system(size: 11, weight: .bold))
                                        .foregroundColor(.white)
                                } else if index == watchBridge.currentConnectionStep {
                                    Circle()
                                        .fill(watchThemeColor)
                                        .frame(width: 24, height: 24)
                                    Circle()
                                        .fill(Color.white)
                                        .frame(width: 8, height: 8)
                                } else {
                                    Circle()
                                        .fill(Color.gray.opacity(0.4))
                                        .frame(width: 10, height: 10)
                                }
                            }
                        }

                        Text(watchSteps[index])
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(index <= watchBridge.currentConnectionStep ? watchThemeColor : Color.gray.opacity(0.6))
                    }
                }
            }
            .padding(.top, 4)

            Text(watchStatusMessage)
                .font(.caption)
                .foregroundColor(watchThemeColor)
                .multilineTextAlignment(.center)
                .padding(.bottom, 4)
        }
    }

    private var watchStatusMessage: String {
        switch watchBridge.currentConnectionStep {
        case 0:
            return "App not installed on Apple Watch"
        case 1:
            return "App installed. Wake your watch or open the app to establish connection."
        case 2, 3:
            return "Watch is connected and ready to capture data."
        default:
            return "Unknown state"
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

struct PostSessionEvaluationView: View {
    @Binding var isPresented: Bool
    let distractions: [String]
    let onSubmit: (Int, [String]) -> Void
    
    @State private var concentrationRating: Double = 5.0
    @State private var selectedDistractions: Set<String> = []
    
    var concentrationColor: Color {
        switch concentrationRating {
        case 1...3: return .red
        case 4...6: return .orange
        case 7...8: return .green
        default: return .blue
        }
    }
    
    var body: some View {
        NavigationView {
            Form {
                Section(
                    header: Text("1. Rate your concentration during this session")
                        .font(.headline)
                        .foregroundColor(.primary)
                        .textCase(nil),
                    footer: Text("1 = Very Poor, 10 = Excellent")
                ) {
                    VStack {
                        Text("\(Int(concentrationRating))")
                            .font(.system(size: 36, weight: .bold))
                            .foregroundColor(concentrationColor)
                            .padding(.bottom, 4)
                        
                        Slider(value: $concentrationRating, in: 1...10, step: 1)
                        
                        HStack {
                            Text("1").font(.caption).foregroundColor(.secondary)
                            Spacer()
                            Text("10").font(.caption).foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 8)
                }
                
                Section(
                    header: Text("2. What distracted you most during this session?")
                        .font(.headline)
                        .foregroundColor(.primary)
                        .textCase(nil)
                ) {
                    ForEach(distractions, id: \.self) { option in
                        Button(action: {
                            if option == "No Major Distraction" {
                                if selectedDistractions.contains(option) {
                                    selectedDistractions.remove(option)
                                } else {
                                    selectedDistractions.removeAll()
                                    selectedDistractions.insert(option)
                                }
                            } else {
                                if selectedDistractions.contains(option) {
                                    selectedDistractions.remove(option)
                                } else {
                                    selectedDistractions.remove("No Major Distraction")
                                    selectedDistractions.insert(option)
                                }
                            }
                        }) {
                            HStack {
                                Text(option)
                                    .foregroundColor(.primary)
                                Spacer()
                                if selectedDistractions.contains(option) {
                                    Image(systemName: "checkmark")
                                        .foregroundColor(.blue)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Session Review")
            .navigationBarItems(
                trailing: Button("Submit") {
                    let formattedDistractions = Array(selectedDistractions).sorted()
                    onSubmit(Int(concentrationRating), formattedDistractions)
                    isPresented = false
                }
                .font(.headline)
                .disabled(selectedDistractions.isEmpty)
            )
        }
    }
}

private struct UserResponseToConcentrationFile: Codable {
    let user_response: [UserResponseQuestionAnswer]
}

private struct UserResponseQuestionAnswer: Codable {
    let question: String
    let answer: UserResponseValue
}

private enum UserResponseValue: Codable {
    case int(Int)
    case string(String)
    case strings([String])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode([String].self) {
            self = .strings(value)
        } else {
            self = .string(try container.decode(String.self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .int(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        case .strings(let value):
            try container.encode(value)
        }
    }
}

private extension ContentView {
    func saveUserResponseToConcentration(rating: Int, distractions: [String]) {
        let payload = UserResponseToConcentrationFile(
            user_response: [
                UserResponseQuestionAnswer(
                    question: "How focused were you during this study session?",
                    answer: .int(rating)
                ),
                UserResponseQuestionAnswer(
                    question: "What most affected your concentration during this session?",
                    answer: .strings(distractions)
                )
            ]
        )

        Task {
            await submitUserResponseToBackend(payload)
        }
    }

    func submitUserResponseToBackend(_ payload: UserResponseToConcentrationFile) async {
        let url = resolveSessionReviewURL()
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.addValue("application/json", forHTTPHeaderField: "Content-Type")
        request.addValue("dev_key", forHTTPHeaderField: "X-API-Key")

        do {
            request.httpBody = try JSONCoding.encoder.encode(payload)
            let (_, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                print("Saved session review JSON to backend path: \(url.absoluteString)")
            } else if let http = response as? HTTPURLResponse {
                print("Failed to save session review JSON. HTTP status=\(http.statusCode)")
            }
        } catch {
            print("Failed to send session review JSON: \(error)")
        }
    }

    func resolveSessionReviewURL() -> URL {
        let configured = (Bundle.main.object(forInfoDictionaryKey: "APIBaseURL") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if let configured,
           !configured.isEmpty,
           !configured.contains("$("),
           !configured.contains("YOUR_MAC_LOCAL_HOSTNAME"),
           let base = URL(string: configured) {
            return base.deletingLastPathComponent().appendingPathComponent("session-review")
        }

#if targetEnvironment(simulator)
        return URL(string: "http://localhost:8000/session-review")!
#else
        return URL(string: "http://YOUR_MAC_LOCAL_HOSTNAME.local:8000/session-review")!
#endif
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
