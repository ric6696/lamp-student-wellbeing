import Foundation
import AVFoundation
import SoundAnalysis

final class AudioClassifier {
    struct Result {
        let label: String
        let confidence: Double
        let timestamp: Date
    }

    static let shared = AudioClassifier()

    private let engine = AVAudioEngine()
    private var analyzer: SNAudioStreamAnalyzer?
    private var observer: AudioClassifierObserver?
    private let analysisQueue = DispatchQueue(label: "audio.classifier.analysis")
    private let resultQueue = DispatchQueue(label: "audio.classifier.results")
    private var latestResult: Result?
    private var isRunning = false

    private init() {}

    func start() {
        guard !isRunning else { return }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: [.mixWithOthers])
            try session.setActive(true, options: .notifyOthersOnDeactivation)
        } catch {
            print("AudioClassifier: failed to configure AVAudioSession: \(error)")
        }

        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        analyzer = SNAudioStreamAnalyzer(format: format)
        observer = AudioClassifierObserver { [weak self] result in
            self?.store(classification: result)
        }

        if #available(iOS 15.0, *) {
            do {
                let request = try SNClassifySoundRequest(classifierIdentifier: .version1)
                if let observer {
                    try analyzer?.add(request, withObserver: observer)
                }
            } catch {
                print("AudioClassifier: failed to add classification request: \(error)")
                analyzer = nil
                observer = nil
                return
            }
        } else {
            print("AudioClassifier: SoundAnalysis requires iOS 15+")
            analyzer = nil
            observer = nil
            return
        }

        inputNode.installTap(onBus: 0, bufferSize: 8192, format: format) { [weak self] buffer, time in
            self?.analysisQueue.async {
                self?.analyzer?.analyze(buffer, atAudioFramePosition: time.sampleTime)
            }
        }

        do {
            try engine.start()
            isRunning = true
        } catch {
            print("AudioClassifier: engine failed to start: \(error)")
            inputNode.removeTap(onBus: 0)
            analyzer = nil
            observer = nil
        }
    }

    func stop() {
        guard isRunning else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        analyzer = nil
        observer = nil
        isRunning = false
    }

    func latestClassification(maxAge: TimeInterval = 5.0) -> Result? {
        resultQueue.sync {
            guard let result = latestResult else { return nil }
            guard Date().timeIntervalSince(result.timestamp) <= maxAge else { return nil }
            return result
        }
    }

    private func store(classification: SNClassificationResult) {
        guard let best = classification.classifications.first else { return }
        let result = Result(label: best.identifier, confidence: best.confidence, timestamp: Date())
        resultQueue.async { [weak self] in
            self?.latestResult = result
        }
    }
}

private final class AudioClassifierObserver: NSObject, SNResultsObserving {
    private let onResult: (SNClassificationResult) -> Void

    init(onResult: @escaping (SNClassificationResult) -> Void) {
        self.onResult = onResult
    }

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let classification = result as? SNClassificationResult else { return }
        onResult(classification)
    }

    func request(_ request: SNRequest, didFailWithError error: Error) {
        print("AudioClassifier: request failed with error: \(error)")
    }

    func requestDidComplete(_ request: SNRequest) {}
}
