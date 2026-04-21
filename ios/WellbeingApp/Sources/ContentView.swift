import SwiftUI

struct ContentView: View {
    static let hktFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        f.timeZone = TimeZone(identifier: "Asia/Hong_Kong") ?? TimeZone(secondsFromGMT: 8 * 3600)
        return f
    }()

    static let hktTimeOnlyFormatter: DateFormatter = {
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
    
    @State private var activityContext: String = ""
    @State private var environmentContext: String = ""
    @State private var mentalContext: String = ""

    @State private var prevActivityContext: String = ""
    @State private var prevEnvironmentContext: String = ""
    @State private var prevMentalContext: String = ""

    @State private var showPostSessionSheet: Bool = false
    @State private var showPreSessionSheet: Bool = false
    @State private var showPredictionReportSheet: Bool = false
    @State private var isLoadingPredictionReport: Bool = false
    @State private var predictionReportError: String?
    @State private var latestPredictionReport: LatestConcentrationReport?
    @State private var prevConcentration: Int? = nil
    @State private var prevDistractions: [String]? = nil
    @State private var sessionHistoryItems: [SessionHistoryItem] = []
    @State private var isLoadingSessionHistory: Bool = false
    @State private var sessionHistoryError: String?
    @State private var sessionHistoryInfoMessage: String?
    @State private var hasLoadedSessionHistory: Bool = false

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
        TabView {
            NavigationView {
                List {
                    Section {
                        focusHeroCard
                    }

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
                }
                .listStyle(InsetGroupedListStyle())
                .navigationTitle("Focus")
            }
            .tabItem {
                Label("Focus", systemImage: "timer")
            }

            NavigationView {
                List {
                    Section(header: Text("Recent Session")) {
                        if let latest = sessionHistoryItems.first {
                            let durationMin = latest.durationMinutes

                            VStack(alignment: .leading, spacing: 6) {
                                Text(formatSessionRange(start: latest.started_at, end: latest.ended_at))
                                    .font(.subheadline.weight(.semibold))
                                Text(String(format: "Duration %.1f min", durationMin))
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }

                            HStack(spacing: 8) {
                                if let score = latest.score {
                                    Text("\(score)/10")
                                        .font(.caption.bold())
                                        .padding(.horizontal, 8)
                                        .padding(.vertical, 4)
                                        .background(Color.blue.opacity(0.12))
                                        .clipShape(Capsule())
                                }
                                if let status = latest.status, !status.isEmpty {
                                    Text(status.capitalized)
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }

                            Button(action: {
                                openPredictionReport(sessionId: latest.session_id, sessionKey: latest.session_key)
                            }) {
                                Label("View Latest Prediction Report", systemImage: "doc.text.magnifyingglass")
                            }
                        } else {
                            Text(isLoadingSessionHistory ? "Loading session history..." : "No previous session yet.")
                                .foregroundColor(.secondary)
                        }
                    }

                    Section(header: Text("Session History")) {
                        if isLoadingSessionHistory && sessionHistoryItems.isEmpty {
                            HStack(spacing: 10) {
                                ProgressView()
                                Text("Fetching sessions...")
                                    .foregroundColor(.secondary)
                            }
                        }

                        if let sessionHistoryError, !sessionHistoryError.isEmpty {
                            Text(sessionHistoryError)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }

                        if let sessionHistoryInfoMessage, !sessionHistoryInfoMessage.isEmpty {
                            Text(sessionHistoryInfoMessage)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }

                        if !sessionHistoryItems.isEmpty {
                            ForEach(sessionHistoryItems) { session in
                                VStack(alignment: .leading, spacing: 8) {
                                    HStack {
                                        Text(formatSessionRange(start: session.started_at, end: session.ended_at))
                                            .font(.subheadline.weight(.semibold))
                                        Spacer()
                                        if let score = session.score {
                                            Text("\(score)/10")
                                                .font(.caption.bold())
                                                .padding(.horizontal, 8)
                                                .padding(.vertical, 4)
                                                .background(Color.blue.opacity(0.12))
                                                .clipShape(Capsule())
                                        }
                                    }

                                    HStack {
                                        Text(String(format: "%.1f min", session.durationMinutes))
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                        if let status = session.status, !status.isEmpty {
                                            Text("• \(status.capitalized)")
                                                .font(.caption)
                                                .foregroundColor(.secondary)
                                        }
                                    }

                                    Button(action: {
                                        openPredictionReport(sessionId: session.session_id, sessionKey: session.session_key)
                                    }) {
                                        Label("View Report", systemImage: "doc.text.magnifyingglass")
                                    }
                                    .font(.caption.weight(.semibold))
                                }
                                .padding(.vertical, 4)
                            }
                        } else if !isLoadingSessionHistory {
                            Text("No completed sessions found yet.")
                                .foregroundColor(.secondary)
                        }

                    }
                }
                .listStyle(InsetGroupedListStyle())
                .navigationTitle("History")
                .task {
                    await loadSessionHistoryIfNeeded()
                }
                .refreshable {
                    await fetchSessionHistory(force: true)
                }
            }
            .tabItem {
                Label("History", systemImage: "clock.arrow.circlepath")
            }
        }
        .sheet(isPresented: $showPostSessionSheet) {
            PostSessionEvaluationView(
                isPresented: $showPostSessionSheet,
                distractions: distractionOptions,
                onSkip: {
                    prevConcentration = nil
                    prevDistractions = nil
                    let sessionKey = latestCompletedSessionKey(from: scheduler.previousSessionItems)
                    openPredictionReport(sessionKey: sessionKey)
                },
                onSubmit: { rating, distractions in
                    prevConcentration = rating
                    prevDistractions = distractions
                    let sessionKey = latestCompletedSessionKey(from: scheduler.previousSessionItems)
                    startPredictionReportFlow(rating: rating, distractions: distractions, sessionKey: sessionKey)
                }
            )
        }
        .sheet(isPresented: $showPreSessionSheet) {
            PreSessionContextView(
                isPresented: $showPreSessionSheet,
                activities: activities,
                environments: environments,
                mentalStates: mentalStates,
                activityContext: $activityContext,
                environmentContext: $environmentContext,
                mentalContext: $mentalContext,
                onSkip: {
                    beginStudySessionFromPreSession(skipContext: true)
                },
                onSubmit: {
                    beginStudySessionFromPreSession(skipContext: false)
                }
            )
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .sheet(isPresented: $showPredictionReportSheet) {
            PredictionReportView(
                isPresented: $showPredictionReportSheet,
                isLoading: isLoadingPredictionReport,
                report: latestPredictionReport,
                errorMessage: predictionReportError
            )
        }
        .onChange(of: scheduler.previousSessionEndTime) { endedAt in
            guard endedAt != nil else { return }
            Task {
                await fetchSessionHistory(force: true)
            }
        }
    }

    private var focusHeroCard: some View {
        VStack(spacing: 16) {
            TimelineView(.periodic(from: .now, by: 1.0)) { timeline in
                let elapsed = elapsedSeconds(at: timeline.date)
                let hourPhase = elapsed.truncatingRemainder(dividingBy: 3600.0) / 3600.0

                ZStack {
                    Circle()
                        .stroke(Color.gray.opacity(0.2), lineWidth: 10)
                    Circle()
                        .trim(from: 0, to: scheduler.isSessionActive ? hourPhase : 0)
                        .stroke(Color.green, style: StrokeStyle(lineWidth: 10, lineCap: .round))
                        .rotationEffect(.degrees(-90))

                    VStack(spacing: 8) {
                        Text(formatHomeElapsedTime(elapsed))
                            .font(.system(size: 52, weight: .bold, design: .rounded))
                            .monospacedDigit()
                            .foregroundColor(scheduler.isSessionActive ? .primary : .secondary)
                        Text(scheduler.isSessionActive ? "Session running" : "Ready to start")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 10)
                }
                .frame(width: 260, height: 260)
                .frame(maxWidth: .infinity)
            }

            if scheduler.isSessionActive {
                Button(action: {
                    endSessionAndPromptFeedback()
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
                    .cornerRadius(12)
                }
            } else {
                let isWatchReady = !watchBridge.enableWatchDataCollection || watchBridge.isReachable
                let canStart = isWatchReady

                Button(action: {
                    if canStart {
                        showPreSessionSheet = true
                    }
                }) {
                    HStack {
                        Image(systemName: canStart ? "play.circle.fill" : "lock.fill")
                        Text(canStart ? "Start Study Session" : "Watch Not Reachable")
                            .bold()
                    }
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(canStart ? Color.green : Color.gray)
                    .foregroundColor(.white)
                    .cornerRadius(12)
                }
                .disabled(!canStart)

                if !isWatchReady {
                    Text("Connect to your Apple Watch to establish a connection before starting the session.")
                        .font(.caption)
                        .foregroundColor(.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .padding(.vertical, 8)
    }

    private func elapsedSeconds(at now: Date) -> TimeInterval {
        guard scheduler.isSessionActive, let start = scheduler.currentSessionStartTime else {
            return 0
        }
        return max(0, now.timeIntervalSince(start))
    }

    private func formatHomeElapsedTime(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds))
        let hours = total / 3600
        let minutes = (total % 3600) / 60
        let secs = total % 60
        if hours > 0 {
            return String(format: "%02d:%02d:%02d", hours, minutes, secs)
        }
        return String(format: "%02d:%02d", minutes, secs)
    }

    private func resolveUserSessionsListURLs(userId: String, limit: Int) -> [URL] {
        var urls: [URL] = []

        let template = (Bundle.main.object(forInfoDictionaryKey: "APISessionsListTemplate") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if let template,
           !template.isEmpty,
           !template.contains("$("),
           template.contains("{user_id}"),
           let url = URL(string: template.replacingOccurrences(of: "{user_id}", with: userId)) {
            var components = URLComponents(url: url, resolvingAgainstBaseURL: false)
            var queryItems = components?.queryItems ?? []
            queryItems.append(URLQueryItem(name: "limit", value: String(limit)))
            components?.queryItems = queryItems
            if let final = components?.url {
                urls.append(final)
            }
        }

        if let base = configuredBaseURL() {
            var components = URLComponents(
                url: base
                    .deletingLastPathComponent()
                    .appendingPathComponent("users")
                    .appendingPathComponent(userId)
                    .appendingPathComponent("sessions"),
                resolvingAgainstBaseURL: false
            )
            components?.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
            if let final = components?.url {
                urls.append(final)
            }
        }

#if targetEnvironment(simulator)
        urls.append(URL(string: "http://localhost:8000/users/\(userId)/sessions?limit=\(limit)")!)
#else
        urls.append(URL(string: "http://YOUR_MAC_LOCAL_HOSTNAME.local:8000/users/\(userId)/sessions?limit=\(limit)")!)
#endif

        return deduplicated(urls)
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

}

struct PostSessionEvaluationView: View {
    @Binding var isPresented: Bool
    let distractions: [String]
    let onSkip: () -> Void
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
                leading: Button("Skip") {
                    onSkip()
                    isPresented = false
                },
                trailing: Button("Submit") {
                    let formattedDistractions = Array(selectedDistractions).sorted()
                    onSubmit(Int(concentrationRating), formattedDistractions)
                    isPresented = false
                }
                .font(.headline)
            )
        }
    }
}

struct PreSessionContextView: View {
    @Binding var isPresented: Bool
    let activities: [String]
    let environments: [String]
    let mentalStates: [String]
    @Binding var activityContext: String
    @Binding var environmentContext: String
    @Binding var mentalContext: String
    let onSkip: () -> Void
    let onSubmit: () -> Void

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Optional Setup")
                        .font(.headline)

                    ContextChipGroup(
                        title: "What are you about to do?",
                        options: activities,
                        selection: $activityContext
                    )

                    ContextChipGroup(
                        title: "Where are you studying?",
                        options: environments,
                        selection: $environmentContext
                    )

                    ContextChipGroup(
                        title: "How ready do you feel mentally?",
                        options: mentalStates,
                        selection: $mentalContext
                    )

                    Text("You can submit these context answers or skip and start immediately.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .padding(16)
            }
            .navigationTitle("Pre-Session Context")
            .navigationBarItems(
                leading: Button("Skip") {
                    onSkip()
                    isPresented = false
                },
                trailing: Button("Start") {
                    onSubmit()
                    isPresented = false
                }
                .font(.headline)
            )
        }
    }
}

private struct ContextChipGroup: View {
    let title: String
    let options: [String]
    @Binding var selection: String

    private let columns = [GridItem(.adaptive(minimum: 110), spacing: 8)]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.subheadline.weight(.semibold))

            LazyVGrid(columns: columns, alignment: .leading, spacing: 8) {
                ForEach(options, id: \.self) { option in
                    let isSelected = selection == option
                    Button(action: {
                        selection = isSelected ? "" : option
                    }) {
                        Text(option)
                            .font(.subheadline)
                            .lineLimit(2)
                            .multilineTextAlignment(.center)
                            .frame(maxWidth: .infinity)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 8)
                            .background(isSelected ? Color.blue.opacity(0.15) : Color(.secondarySystemFill))
                            .foregroundColor(isSelected ? .blue : .primary)
                            .clipShape(Capsule())
                            .overlay(
                                Capsule()
                                    .stroke(isSelected ? Color.blue : Color.clear, lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

private struct UserResponseToConcentrationFile: Codable {
    let user_response: [UserResponseQuestionAnswer]
    let session_key: String?
}

private struct SessionReviewResponse: Codable {
    let status: String?
    let path: String?
    let session_id: Int?
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

private struct UserSessionsListResponse: Codable {
    let items: [SessionHistoryItem]
    let count: Int
}

private struct SessionHistoryItem: Codable, Identifiable {
    let session_id: Int
    let session_key: String?
    let started_at: Date
    let ended_at: Date
    let status: String?
    let score: Int?
    let reason: String?
    let error_message: String?

    var id: Int { session_id }

    var durationMinutes: Double {
        max(ended_at.timeIntervalSince(started_at), 1) / 60
    }
}

private struct LatestUserSessionHistorySnapshot: Codable {
    let session_id: Int?
    let session_key: String?
    let started_at: Date?
    let ended_at: Date?
    let status: String?
    let score: Int?
    let reason: String?
    let error_message: String?
}

private extension ContentView {
    func formatSessionRange(start: Date, end: Date) -> String {
        let calendar = Calendar.current
        if calendar.isDate(start, inSameDayAs: end) {
            return "\(ContentView.hktFormatter.string(from: start)) - \(ContentView.hktTimeOnlyFormatter.string(from: end))"
        }

        return "\(ContentView.hktFormatter.string(from: start)) - \(ContentView.hktFormatter.string(from: end))"
    }

    func beginStudySessionFromPreSession(skipContext: Bool) {
        if skipContext {
            activityContext = ""
            environmentContext = ""
            mentalContext = ""
        }
        scheduler.startStudySession()
    }

    func endSessionAndPromptFeedback() {
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
    }

    func loadSessionHistoryIfNeeded() async {
        if !hasLoadedSessionHistory {
            await fetchSessionHistory(force: false)
        }
    }

    func recentSevenDaySessions(from items: [SessionHistoryItem]) -> [SessionHistoryItem] {
        let calendar = Calendar.current
        let fallbackCutoff = Date().addingTimeInterval(-7 * 24 * 60 * 60)
        let cutoff = calendar.date(byAdding: .day, value: -7, to: Date()) ?? fallbackCutoff

        return items
            .filter { max($0.ended_at, $0.started_at) >= cutoff }
            .sorted { $0.ended_at > $1.ended_at }
    }

    func fetchSessionHistory(force: Bool) async {
        if isLoadingSessionHistory { return }
        if hasLoadedSessionHistory && !force { return }

        await MainActor.run {
            isLoadingSessionHistory = true
            sessionHistoryError = nil
            sessionHistoryInfoMessage = nil
        }

        let urls = resolveUserSessionsListURLs(userId: AppIdentity.userId.lowercased(), limit: 200)
        var unauthorized = false
        var endpointUnavailable = false

        for url in urls {
            var request = URLRequest(url: url)
            request.httpMethod = "GET"
            request.addValue("dev_key", forHTTPHeaderField: "X-API-Key")

            do {
                let (data, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse else {
                    continue
                }

                if (200..<300).contains(http.statusCode) {
                    let payload = try JSONCoding.decoder.decode(UserSessionsListResponse.self, from: data)
                    let recentItems = recentSevenDaySessions(from: payload.items)
                    await MainActor.run {
                        sessionHistoryItems = recentItems
                        isLoadingSessionHistory = false
                        hasLoadedSessionHistory = true
                        sessionHistoryError = nil
                    }
                    return
                }

                if http.statusCode == 401 {
                    unauthorized = true
                } else if http.statusCode == 404 || http.statusCode == 400 {
                    endpointUnavailable = true
                }
            } catch {
                continue
            }
        }

        if let latestFallback = await fetchLatestSessionHistoryFallback() {
            let recentFallbackItems = recentSevenDaySessions(from: [latestFallback])
            await MainActor.run {
                sessionHistoryItems = recentFallbackItems
                isLoadingSessionHistory = false
                hasLoadedSessionHistory = true
                sessionHistoryError = nil
                if endpointUnavailable {
                    sessionHistoryInfoMessage = "Showing latest session only. Full history endpoint is not available on this server build yet."
                }
            }
            return
        }

        await MainActor.run {
            isLoadingSessionHistory = false
            hasLoadedSessionHistory = true
            if unauthorized {
                sessionHistoryError = "History is unavailable: unauthorized request."
            } else if endpointUnavailable {
                sessionHistoryError = "Full session history endpoint is not available on this server build yet."
            } else {
                sessionHistoryError = "Could not load full session history."
            }
        }
    }

    func fetchLatestSessionHistoryFallback() async -> SessionHistoryItem? {
        let urls = resolveLatestUserConcentrationURLs(userId: AppIdentity.userId.lowercased())

        for url in urls {
            var request = URLRequest(url: url)
            request.httpMethod = "GET"
            request.addValue("dev_key", forHTTPHeaderField: "X-API-Key")

            do {
                let (data, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse,
                      (200..<300).contains(http.statusCode) else {
                    continue
                }

                let payload = try JSONCoding.decoder.decode(LatestUserSessionHistorySnapshot.self, from: data)
                guard let startedAt = payload.started_at,
                      let endedAt = payload.ended_at else {
                    continue
                }

                return SessionHistoryItem(
                    session_id: payload.session_id ?? -1,
                    session_key: payload.session_key,
                    started_at: startedAt,
                    ended_at: endedAt,
                    status: payload.status,
                    score: payload.score,
                    reason: payload.reason,
                    error_message: payload.error_message
                )
            } catch {
                continue
            }
        }

        return nil
    }

    func startPredictionReportFlow(rating: Int, distractions: [String], sessionKey: String?) {
        preparePredictionReportPresentation()

        Task {
            let sessionId = await saveUserResponseToConcentration(
                rating: rating,
                distractions: distractions,
                sessionKey: sessionKey
            )
            guard sessionId != nil || sessionKey != nil else {
                await MainActor.run {
                    isLoadingPredictionReport = false
                    predictionReportError = "Could not submit session review. Please try again."
                }
                return
            }

            await loadLatestPredictionReport(sessionId: sessionId, sessionKey: sessionKey)
        }
    }

    func openPredictionReport(sessionKey: String?) {
        openPredictionReport(sessionId: nil, sessionKey: sessionKey)
    }

    func openPredictionReport(sessionId: Int?, sessionKey: String?) {
        preparePredictionReportPresentation()

        Task {
            await loadLatestPredictionReport(sessionId: sessionId, sessionKey: sessionKey)
        }
    }

    func preparePredictionReportPresentation() {
        isLoadingPredictionReport = true
        predictionReportError = nil
        latestPredictionReport = nil

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            showPredictionReportSheet = true
        }
    }

    func saveUserResponseToConcentration(rating: Int, distractions: [String], sessionKey: String?) async -> Int? {
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
            ],
            session_key: sessionKey
        )

        return await submitUserResponseToBackend(payload)
    }

    func submitUserResponseToBackend(_ payload: UserResponseToConcentrationFile) async -> Int? {
        let encodedBody: Data
        do {
            encodedBody = try JSONCoding.encoder.encode(payload)
        } catch {
            print("Failed to encode session review JSON: \(error)")
            return nil
        }

        for url in resolveSessionReviewURLs() {
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.addValue("application/json", forHTTPHeaderField: "Content-Type")
            request.addValue("dev_key", forHTTPHeaderField: "X-API-Key")
            request.httpBody = encodedBody

            do {
                let (data, response) = try await URLSession.shared.data(for: request)
                if let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                    if let parsed = try? JSONCoding.decoder.decode(SessionReviewResponse.self, from: data) {
                        print("Saved session review JSON to backend path: \(url.absoluteString)")
                        return parsed.session_id
                    }
                    print("Saved session review JSON to backend path: \(url.absoluteString)")
                    return nil
                } else if let http = response as? HTTPURLResponse {
                    print("Failed to save session review JSON at \(url.absoluteString). HTTP status=\(http.statusCode)")
                    if http.statusCode == 401 {
                        return nil
                    }
                }
            } catch {
                print("Failed to send session review JSON to \(url.absoluteString): \(error)")
            }
        }

        return nil
    }

    func loadLatestPredictionReport(sessionId: Int?, sessionKey: String?) async {
        let candidateURLs: [URL]
        if let sessionId {
            // When we have a session id, hit the direct endpoint only to avoid slow fallbacks.
            candidateURLs = resolveSessionConcentrationByIdURLs(sessionId: sessionId)
        } else {
            candidateURLs = resolveConcentrationReportURLs(sessionId: sessionId, sessionKey: sessionKey)
        }

        let attempts = 40
        let delayNanoseconds: UInt64 = 1_000_000_000

        for attempt in 1...attempts {
            var unauthorized = false

            for url in candidateURLs {
                var request = URLRequest(url: url)
                request.httpMethod = "GET"
                request.timeoutInterval = 5
                request.addValue("dev_key", forHTTPHeaderField: "X-API-Key")

                do {
                    let (data, response) = try await URLSession.shared.data(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        continue
                    }

                    if (200..<300).contains(http.statusCode) {
                        let report = try JSONCoding.decoder.decode(LatestConcentrationReport.self, from: data)
                        let normalizedStatus = report.status?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
                        let finishedStatuses: Set<String> = ["done", "completed", "succeeded", "success"]
                        let hasUsefulResult = report.score != nil
                            || (report.reason?.isEmpty == false)
                            || (report.phase_2?.holistic_assessment?.isEmpty == false)
                            || (report.phase_3?.recommendations?.isEmpty == false)

                        if let normalizedStatus,
                           !finishedStatuses.contains(normalizedStatus),
                           !hasUsefulResult {
                            continue
                        }

                        await MainActor.run {
                            latestPredictionReport = report
                            isLoadingPredictionReport = false
                            predictionReportError = nil
                        }
                        return
                    }

                    if http.statusCode == 401 {
                        unauthorized = true
                    }
                } catch {
                    continue
                }
            }

            if unauthorized {
                await MainActor.run {
                    isLoadingPredictionReport = false
                    predictionReportError = "Unauthorized request. Please verify EC2 API key configuration."
                }
                return
            }

            if attempt < attempts {
                try? await Task.sleep(nanoseconds: delayNanoseconds)
            }
        }

        await MainActor.run {
            isLoadingPredictionReport = false
            predictionReportError = "Prediction report is not ready yet. Please try again in a moment."
        }
    }

    func configuredURL(for key: String) -> URL? {
        let configured = (Bundle.main.object(forInfoDictionaryKey: key) as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if let configured,
           !configured.isEmpty,
           !configured.contains("$("),
           !configured.contains("YOUR_MAC_LOCAL_HOSTNAME"),
           let url = URL(string: configured) {
            return url
        }

        return nil
    }

    func configuredBaseURL() -> URL? {
        let configured = (Bundle.main.object(forInfoDictionaryKey: "APIBaseURL") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if let configured,
           !configured.isEmpty,
           !configured.contains("$("),
           !configured.contains("YOUR_MAC_LOCAL_HOSTNAME"),
           let base = URL(string: configured) {
            return base
        }

        return nil
    }

    func resolveSessionReviewURLs() -> [URL] {
        var urls: [URL] = []

        if let explicit = configuredURL(for: "APISessionReviewURL") {
            urls.append(explicit)
        }

        if let base = configuredBaseURL() {
            urls.append(base.deletingLastPathComponent().appendingPathComponent("session-review"))
        }

#if targetEnvironment(simulator)
        urls.append(URL(string: "http://localhost:8000/session-review")!)
#else
        urls.append(URL(string: "http://YOUR_MAC_LOCAL_HOSTNAME.local:8000/session-review")!)
#endif

        return deduplicated(urls)
    }

    func resolveLatestConcentrationURLs() -> [URL] {
        var urls: [URL] = []

        if let explicit = configuredURL(for: "APIConcentrationReportURL") {
            urls.append(explicit)
        }

        if let base = configuredBaseURL() {
            urls.append(base.deletingLastPathComponent().appendingPathComponent("latest-concentration-report"))
        }

#if targetEnvironment(simulator)
        urls.append(URL(string: "http://localhost:8000/latest-concentration-report")!)
#else
        urls.append(URL(string: "http://YOUR_MAC_LOCAL_HOSTNAME.local:8000/latest-concentration-report")!)
#endif

        return deduplicated(urls)
    }

    func resolveLatestUserConcentrationURLs(userId: String) -> [URL] {
        var urls: [URL] = []

        let template = (Bundle.main.object(forInfoDictionaryKey: "APILatestUserConcentrationTemplate") as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if let template,
           !template.isEmpty,
           !template.contains("$("),
           template.contains("{user_id}"),
           let url = URL(string: template.replacingOccurrences(of: "{user_id}", with: userId)) {
            urls.append(url)
        }

        if let base = configuredBaseURL() {
            urls.append(
                base
                    .deletingLastPathComponent()
                    .appendingPathComponent("users")
                    .appendingPathComponent(userId)
                    .appendingPathComponent("sessions")
                    .appendingPathComponent("latest")
                    .appendingPathComponent("concentration")
            )
        }

        return deduplicated(urls)
    }

    func resolveSessionConcentrationByIdURLs(sessionId: Int) -> [URL] {
        var urls: [URL] = []

        if let base = configuredBaseURL() {
            urls.append(
                base
                    .deletingLastPathComponent()
                    .appendingPathComponent("sessions")
                    .appendingPathComponent(String(sessionId))
                    .appendingPathComponent("concentration")
            )
        }

#if targetEnvironment(simulator)
        urls.append(URL(string: "http://localhost:8000/sessions/\(sessionId)/concentration")!)
#else
        urls.append(URL(string: "http://YOUR_MAC_LOCAL_HOSTNAME.local:8000/sessions/\(sessionId)/concentration")!)
#endif

        return deduplicated(urls)
    }

    func resolveConcentrationReportURLs(sessionId: Int?, sessionKey: String?) -> [URL] {
        var urls: [URL] = []
        let userId = AppIdentity.userId.lowercased()

        if let sessionId {
            urls.append(contentsOf: resolveSessionConcentrationByIdURLs(sessionId: sessionId))
        }

        if let sessionKey, !sessionKey.isEmpty {
            let template = (Bundle.main.object(forInfoDictionaryKey: "APIConcentrationBySessionKeyTemplate") as? String)?
                .trimmingCharacters(in: .whitespacesAndNewlines)

            if let template,
               !template.isEmpty,
               !template.contains("$("),
               template.contains("{session_key}"),
               let templated = URL(string: template.replacingOccurrences(of: "{session_key}", with: sessionKey)) {
                urls.append(templated)
            }

            if let base = configuredBaseURL() {
                urls.append(
                    base
                        .deletingLastPathComponent()
                        .appendingPathComponent("sessions")
                        .appendingPathComponent("by-key")
                        .appendingPathComponent(sessionKey)
                        .appendingPathComponent("concentration-report")
                )
            }

#if targetEnvironment(simulator)
            urls.append(URL(string: "http://localhost:8000/sessions/by-key/\(sessionKey)/concentration-report")!)
#else
            urls.append(URL(string: "http://YOUR_MAC_LOCAL_HOSTNAME.local:8000/sessions/by-key/\(sessionKey)/concentration-report")!)
#endif
        }

        urls.append(contentsOf: resolveLatestUserConcentrationURLs(userId: userId))

        urls.append(contentsOf: resolveLatestConcentrationURLs())
        return deduplicated(urls)
    }

    func deduplicated(_ urls: [URL]) -> [URL] {
        var seen: Set<String> = []
        var unique: [URL] = []
        for url in urls {
            let key = url.absoluteString
            if seen.insert(key).inserted {
                unique.append(url)
            }
        }
        return unique
    }

    func latestCompletedSessionKey(from items: [BatchItem]) -> String? {
        for item in items.reversed() {
            guard item.type == .event,
                  item.label == "session_marker",
                  (item.val_text ?? "").uppercased() == "END",
                  let metadata = item.metadata,
                  let sessionKey = metadata["session_key"]?.stringValue,
                  !sessionKey.isEmpty else {
                continue
            }
            return sessionKey.lowercased()
        }
        return nil
    }
}

private struct LatestConcentrationReport: Codable {
    private struct FlexibleInt: Codable {
        let value: Int

        init(from decoder: Decoder) throws {
            let container = try decoder.singleValueContainer()
            if let intValue = try? container.decode(Int.self) {
                value = intValue
                return
            }
            if let doubleValue = try? container.decode(Double.self) {
                value = Int(doubleValue.rounded())
                return
            }
            if let stringValue = try? container.decode(String.self),
               let parsed = Int(stringValue.trimmingCharacters(in: .whitespacesAndNewlines)) {
                value = parsed
                return
            }
            throw DecodingError.typeMismatch(Int.self, DecodingError.Context(codingPath: container.codingPath, debugDescription: "Unable to decode score as Int"))
        }

        func encode(to encoder: Encoder) throws {
            var container = encoder.singleValueContainer()
            try container.encode(value)
        }
    }

    struct Phase2: Codable {
        let holistic_assessment: String?

        init(holistic_assessment: String?) {
            self.holistic_assessment = holistic_assessment
        }

        init(from decoder: Decoder) throws {
            if let single = try? decoder.singleValueContainer(),
               let text = try? single.decode(String.self) {
                self.holistic_assessment = text
                return
            }

            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.holistic_assessment = try container.decodeIfPresent(String.self, forKey: .holistic_assessment)
        }
    }

    struct Phase3: Codable {
        let recommendations: String?

        init(recommendations: String?) {
            self.recommendations = recommendations
        }

        init(from decoder: Decoder) throws {
            if let single = try? decoder.singleValueContainer(),
               let text = try? single.decode(String.self) {
                self.recommendations = text
                return
            }

            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.recommendations = try container.decodeIfPresent(String.self, forKey: .recommendations)
        }
    }

    private enum CodingKeys: String, CodingKey {
        case session_id
        case session_key
        case status
        case score
        case reason
        case error_message
        case phase_2
        case phase_3
    }

    let session_id: Int?
    let session_key: String?
    let status: String?
    let score: Int?
    let reason: String?
    let error_message: String?
    let phase_2: Phase2?
    let phase_3: Phase3?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        session_id = try container.decodeIfPresent(Int.self, forKey: .session_id)
        session_key = try container.decodeIfPresent(String.self, forKey: .session_key)
        status = try container.decodeIfPresent(String.self, forKey: .status)
        reason = try container.decodeIfPresent(String.self, forKey: .reason)
        error_message = try container.decodeIfPresent(String.self, forKey: .error_message)

        if let intValue = try? container.decodeIfPresent(Int.self, forKey: .score) {
            score = intValue
        } else if let flexible = try? container.decodeIfPresent(FlexibleInt.self, forKey: .score) {
            score = flexible.value
        } else {
            score = nil
        }

        if let parsedPhase2 = try? container.decodeIfPresent(Phase2.self, forKey: .phase_2) {
            phase_2 = parsedPhase2
        } else if let fallbackPhase2Text = try? container.decodeIfPresent(String.self, forKey: .phase_2) {
            phase_2 = Phase2(holistic_assessment: fallbackPhase2Text)
        } else {
            phase_2 = nil
        }

        if let parsedPhase3 = try? container.decodeIfPresent(Phase3.self, forKey: .phase_3) {
            phase_3 = parsedPhase3
        } else if let fallbackPhase3Text = try? container.decodeIfPresent(String.self, forKey: .phase_3) {
            phase_3 = Phase3(recommendations: fallbackPhase3Text)
        } else {
            phase_3 = nil
        }
    }
}

private struct PredictionReportView: View {
    @Binding var isPresented: Bool
    let isLoading: Bool
    let report: LatestConcentrationReport?
    let errorMessage: String?

    private var scoreValue: Int {
        guard let report else { return 0 }
        return max(0, min(report.score ?? 0, 10))
    }

    private var scoreProgress: Double {
        Double(scoreValue) / 10.0
    }

    private var scoreColor: Color {
        switch scoreValue {
        case 0...3: return .red
        case 4...6: return .orange
        case 7...8: return .green
        default: return .blue
        }
    }

    private var scoreLabel: String {
        switch scoreValue {
        case 0...3: return "Needs Support"
        case 4...6: return "Fair Focus"
        case 7...8: return "Good Focus"
        default: return "Excellent Focus"
        }
    }

    var body: some View {
        NavigationView {
            ZStack {
                Color(.systemGroupedBackground)
                    .ignoresSafeArea()

                if isLoading {
                    VStack(spacing: 14) {
                        ProgressView()
                            .scaleEffect(1.1)
                        Text("Preparing your prediction report...")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let errorMessage {
                    VStack(spacing: 12) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.system(size: 28))
                            .foregroundColor(.orange)
                        Text(errorMessage)
                            .font(.body)
                            .multilineTextAlignment(.center)
                            .foregroundColor(.secondary)
                    }
                    .padding(24)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let report {
                    ScrollView {
                        VStack(spacing: 14) {
                            scoreCard(report: report)

                            detailCard(
                                title: "LLM Reasoning",
                                icon: "brain.head.profile",
                                content: report.phase_2?.holistic_assessment,
                                fallback: "No detailed reasoning available yet."
                            )

                            detailCard(
                                title: "Study Advice",
                                icon: "lightbulb.max",
                                content: report.phase_3?.recommendations,
                                fallback: "No advice available yet."
                            )
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 14)
                    }
                } else {
                    VStack(spacing: 12) {
                        ProgressView()
                        Text("Prediction is still being generated...")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                        Text("Please wait a few seconds. This is normal for a new session.")
                            .font(.caption)
                            .multilineTextAlignment(.center)
                            .foregroundColor(.secondary)
                    }
                    .padding()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .navigationTitle("Session Prediction")
            .navigationBarItems(trailing: Button("Done") { isPresented = false })
        }
    }

    @ViewBuilder
    private func scoreCard(report: LatestConcentrationReport) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Prediction Score")
                        .font(.headline)
                    Text("How your concentration looked this session")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Text(scoreLabel)
                    .font(.caption.bold())
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(scoreColor.opacity(0.15))
                    .foregroundColor(scoreColor)
                    .clipShape(Capsule())
            }

            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text("\(scoreValue)")
                    .font(.system(size: 44, weight: .bold, design: .rounded))
                    .foregroundColor(scoreColor)
                Text("/10")
                    .font(.title3.weight(.semibold))
                    .foregroundColor(.secondary)
            }

            ProgressView(value: scoreProgress)
                .tint(scoreColor)

            if let status = report.status, !status.isEmpty {
                Text("Status: \(status.capitalized)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            if let reason = report.reason, !reason.isEmpty {
                Text(reason)
                    .font(.body)
                    .foregroundColor(.primary)
                    .lineSpacing(4)
            } else if let error = report.error_message, !error.isEmpty {
                Text(error)
                    .font(.body)
                    .foregroundColor(.secondary)
                    .lineSpacing(4)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    @ViewBuilder
    private func detailCard(title: String, icon: String, content: String?, fallback: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: icon)
                .font(.headline)

            Text(normalizedText(content, fallback: fallback))
                .font(.body)
                .foregroundColor(content?.isEmpty == false ? .primary : .secondary)
                .lineSpacing(5)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private func normalizedText(_ text: String?, fallback: String) -> String {
        guard let text else { return fallback }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? fallback : trimmed
    }
}

private struct SessionDataView: View {
    let isSessionActive: Bool
    let watchEnabled: Bool
    let watchReachable: Bool
    let watchConnectivityText: String
    let currentSessionStartTime: Date?
    let previousSessionStartTime: Date?
    let previousSessionEndTime: Date?
    let prevActivityContext: String
    let prevEnvironmentContext: String
    let prevMentalContext: String
    let prevConcentration: Int?
    let prevDistractions: [String]?
    let items: [BatchItem]
    let onViewLastReport: (String?) -> Void

    private var sessionDurationMinutes: Double {
        if let start = previousSessionStartTime, let end = previousSessionEndTime {
            return max(end.timeIntervalSince(start), 1) / 60
        }
        if let first = items.first?.t, let last = items.last?.t {
            return max(last.timeIntervalSince(first), 1) / 60
        }
        return 0
    }

    private var sensorStats: [String: SensorStat] {
        var stats: [String: SensorStat] = [:]
        for definition in ContentView.sensorDefinitions {
            let count = countItems(for: definition)
            let frequency = sessionDurationMinutes > 0 ? Double(count) / sessionDurationMinutes : 0
            stats[definition.id] = SensorStat(count: count, frequencyPerMinute: frequency)
        }
        return stats
    }

    var body: some View {
        List {
            Section(header: Text("Sensor Sources")) {
                SensorSourceCard(
                    title: "iPhone",
                    subtitle: isSessionActive ? "Streaming data" : "Idle until the next session",
                    iconName: "iphone.gen3",
                    isActive: isSessionActive
                )
                if watchEnabled {
                    SensorSourceCard(
                        title: "Apple Watch",
                        subtitle: watchConnectivityText,
                        iconName: "applewatch",
                        isActive: isSessionActive && watchReachable
                    )
                }
            }

            Section(header: Text("Previous Session")) {
                if let start = previousSessionStartTime {
                    let startStr = ContentView.hktFormatter.string(from: start)
                    let endStr = previousSessionEndTime != nil ? ContentView.hktFormatter.string(from: previousSessionEndTime!) : "?"
                    Text("Started: \(startStr)   Ended: \(endStr)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                if !prevActivityContext.isEmpty || !prevEnvironmentContext.isEmpty || !prevMentalContext.isEmpty {
                    Text("\(prevActivityContext) • \(prevEnvironmentContext) • \(prevMentalContext)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                if let conc = prevConcentration, let dist = prevDistractions, !dist.isEmpty {
                    Text("Concentration: \(conc)/10 • Distracted by: \(dist.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                HStack {
                    Text("Total Samples")
                    Spacer()
                    Text("\(items.count)")
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
                    let freq = sessionDurationMinutes > 0 ? Double(items.count) / sessionDurationMinutes : 0
                    Text(String(format: "%.2f/min", freq))
                        .foregroundColor(.secondary)
                }
            }

            Section(header: Text("Prediction Report")) {
                let sessionKey = latestCompletedSessionKey(from: items)
                Button(action: {
                    onViewLastReport(sessionKey)
                }) {
                    Label("View Last Prediction Report", systemImage: "doc.text.magnifyingglass")
                }
                Text(sessionKey == nil ? "Session key not found. The app will try the latest available report." : "Opens the report linked to your most recent completed study session.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            ForEach(SensorDisplayType.SourceCategory.allCases, id: \.self) { source in
                let definitions = ContentView.sensorDefinitions.filter { $0.source == source }
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
        .navigationTitle("Session Data")
    }

    private func countItems(for definition: SensorDisplayType) -> Int {
        switch definition.kind {
        case .vital(let code):
            return items.filter { $0.type == .vital && $0.code == code }.count
        case .event(let label):
            return items.filter { $0.type == .event && $0.label == label }.count
        case .gps:
            return items.filter { $0.type == .gps }.count
        }
    }

    private func items(for definition: SensorDisplayType) -> [BatchItem] {
        switch definition.kind {
        case .vital(let code):
            return items.filter { $0.type == .vital && $0.code == code }
                .sorted { $0.t > $1.t }
        case .event(let label):
            return items.filter { $0.type == .event && $0.label == label }
                .sorted { $0.t > $1.t }
        case .gps:
            return items.filter { $0.type == .gps }
                .sorted { $0.t > $1.t }
        }
    }

    private func latestCompletedSessionKey(from items: [BatchItem]) -> String? {
        for item in items.reversed() {
            guard item.type == .event,
                  item.label == "session_marker",
                  (item.val_text ?? "").uppercased() == "END",
                  let metadata = item.metadata,
                  let sessionKey = metadata["session_key"]?.stringValue,
                  !sessionKey.isEmpty else {
                continue
            }
            return sessionKey.lowercased()
        }
        return nil
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
