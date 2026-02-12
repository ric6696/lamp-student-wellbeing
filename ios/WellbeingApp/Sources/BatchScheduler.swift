import Foundation

final class BatchScheduler: ObservableObject {
    enum Reason { case timer, appOpen, manual }

    @Published var isEnabled: Bool = true {
        didSet {
            if isEnabled { resume() }
            else { stop() }
        }
    }

    private let interval: TimeInterval
    private var timer: Timer?
    private let api: APIClient

    init(intervalMinutes: Double) {
        self.interval = intervalMinutes * 60
        self.api = APIClient(baseURL: URL(string: "http://10.89.237.157:8000/ingest")!, deviceId: DeviceId.value)
    }

    func resume() {
        timer?.invalidate()
        guard isEnabled else { return }
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { _ = await self?.flushIfNeeded(reason: .timer) }
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    @discardableResult
    func flushIfNeeded(reason: Reason) async -> Bool {
        guard isEnabled || reason == .manual else { return false }
        await SensorCollector.shared.collect()

        do {
            let items = try LocalStore.shared.drain(limit: 100)
            guard await api.send(items: items) else {
                try LocalStore.shared.append(items)
                return false
            }
            return true
        } catch {
            print("Flush failed: \(error)")
            return false
        }
    }
}
