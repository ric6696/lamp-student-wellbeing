import Foundation
import AVFoundation
import SoundAnalysis

final class NoiseManager {
    struct AudioContextSnapshot {
        let label: String
        let confidence: Double
        let timestamp: Date
        let decibel: Float
        let labelSource: String
        let aiLabel: String?
        let aiConfidence: Double?
        let heuristicLabel: String
    }

    static let shared = NoiseManager()
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private(set) var currentDb: Float = 0
    private let contextQueue = DispatchQueue(label: "audio.context.queue")
    private var latestContext: AudioContextSnapshot?

    private init() {
        let url = URL(fileURLWithPath: "/dev/null")
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatAppleLossless),
            AVSampleRateKey: 44100.0,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.min.rawValue
        ]
        
        try? AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .measurement, options: .mixWithOthers)
        recorder = try? AVAudioRecorder(url: url, settings: settings)
    }

    func start() {
        guard let recorder = recorder else { return }
        AudioClassifier.shared.start()
        recorder.isMeteringEnabled = true
        recorder.record()
        
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            recorder.updateMeters()
            // Map -160..0 dB to a more readable range
            guard let db = self?.normalizedDb(from: recorder.averagePower(forChannel: 0)) else { return }
            self?.currentDb = db
            self?.storeContextSample(db: db)
        }
    }

    func stop() {
        timer?.invalidate()
        recorder?.stop()
        AudioClassifier.shared.stop()
    }

    func latestAudioContext(maxAge: TimeInterval = 8.0) -> AudioContextSnapshot? {
        contextQueue.sync {
            guard let snapshot = latestContext,
                  Date().timeIntervalSince(snapshot.timestamp) <= maxAge else {
                return nil
            }
            return snapshot
        }
    }

    private func normalizedDb(from rawValue: Float) -> Float {
        // Clamp to [-90, 0] to reduce spikes from the recorder and make heuristics predictable
        return max(-90, min(0, rawValue))
    }

    private func storeContextSample(db: Float) {
        let timestamp = Date()
        let heuristic = label(for: db)
        let aiResult = AudioClassifier.shared.latestClassification()
        var finalLabel = heuristic
        var finalConfidence = confidence(for: db)
        var source = "heuristic"
        if let aiResult {
            finalLabel = aiResult.label
            finalConfidence = aiResult.confidence
            source = "sound_analysis"
        }

        let sample = AudioContextSnapshot(
            label: finalLabel,
            confidence: finalConfidence,
            timestamp: timestamp,
            decibel: db,
            labelSource: source,
            aiLabel: aiResult?.label,
            aiConfidence: aiResult?.confidence,
            heuristicLabel: heuristic
        )
        contextQueue.async { [weak self] in
            self?.latestContext = sample
        }
    }

    private func label(for db: Float) -> String {
        switch db {
        case ..<(-55):
            return "quiet"
        case -55..<(-40):
            return "moderate"
        case -40..<(-25):
            return "busy"
        default:
            return "very_loud"
        }
    }

    private func confidence(for db: Float) -> Double {
        // Map the clamped [-90, 0] range to [0, 1]
        let normalized = (Double(db) + 90.0) / 90.0
        return max(0, min(1, normalized))
    }
}
