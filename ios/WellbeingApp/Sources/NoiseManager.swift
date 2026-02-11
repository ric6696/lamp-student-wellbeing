import Foundation
import AVFoundation

final class NoiseManager {
    static let shared = NoiseManager()
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private(set) var currentDb: Float = 0

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
        recorder.isMeteringEnabled = true
        recorder.record()
        
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            recorder.updateMeters()
            // Map -160..0 dB to a more readable range
            self?.currentDb = recorder.averagePower(forChannel: 0)
        }
    }

    func stop() {
        timer?.invalidate()
        recorder?.stop()
    }
}
