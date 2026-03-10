import Foundation

struct APIClient {
    let baseURL: URL
    let userId: String
    let deviceId: String
    private let session: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 15
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }()

    func send(items: [BatchItem]) async -> Bool {
        guard !items.isEmpty else { return true }
        print("APIClient: baseURL=")
        print(baseURL.absoluteString)
        await probeConnectivity(context: "send")
        let normalizedItems = items.map { item -> BatchItem in
            var normalized = item
            let effectiveDeviceId = (item.device_id ?? deviceId).lowercased()
            normalized.device_id = effectiveDeviceId == deviceId.lowercased() ? nil : effectiveDeviceId
            return normalized
        }
        let distinctDeviceIds = Set(normalizedItems.map { $0.device_id ?? deviceId.lowercased() }).sorted()
        print("APIClient: send total_items=\(items.count) envelope_device_id=\(deviceId.lowercased()) reading_devices=\(distinctDeviceIds) user_id=\(userId)")

        let envelope = BatchEnvelope(
            metadata: .init(device_id: deviceId.lowercased(), user_id: userId, model_name: nil),
            data: normalizedItems
        )

        print("APIClient: sending envelope device_id=\(deviceId.lowercased()) item_count=\(normalizedItems.count)")
        let sent = await sendEnvelope(envelope)
        print("APIClient: envelope send \(sent ? "succeeded" : "failed") device_id=\(deviceId.lowercased())")
        return sent
    }

    func probeConnectivity(context: String) async {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            print("APIClient: health check skipped context=\(context) invalid baseURL=\(baseURL.absoluteString)")
            return
        }
        components.path = "/health"
        components.query = nil
        components.fragment = nil
        guard let healthURL = components.url else {
            print("APIClient: health check skipped context=\(context) could not build health URL from \(baseURL.absoluteString)")
            return
        }

        var request = URLRequest(url: healthURL)
        request.httpMethod = "GET"
        request.timeoutInterval = 5

        do {
            let (data, response) = try await session.data(for: request)
            if let http = response as? HTTPURLResponse {
                let body = String(data: data, encoding: .utf8) ?? ""
                print("APIClient: health check context=\(context) status=\(http.statusCode) url=\(healthURL.absoluteString) body=\(body)")
            } else {
                print("APIClient: health check context=\(context) received non-HTTP response url=\(healthURL.absoluteString)")
            }
        } catch {
            let nsError = error as NSError
            print("APIClient: health check failed context=\(context) domain=\(nsError.domain) code=\(nsError.code) description=\(nsError.localizedDescription) url=\(healthURL.absoluteString)")
        }
    }

    private func sendEnvelope(_ envelope: BatchEnvelope) async -> Bool {
        var request = URLRequest(url: baseURL)
        print("APIClient: sending POST to \(baseURL.absoluteString)")
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        request.addValue("application/json", forHTTPHeaderField: "Content-Type")
        request.addValue("dev_key", forHTTPHeaderField: "X-API-Key")

        let jsonData = try? JSONCoding.encoder.encode(envelope)
        if let jsonData = jsonData, let str = String(data: jsonData, encoding: .utf8) {
            print("Sending payload: \(str)")
        }
        request.httpBody = jsonData

        do {
            let (data, response) = try await session.data(for: request)
            if let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                print("APIClient: HTTP success status=\(http.statusCode)")
                return true
            }
            if let http = response as? HTTPURLResponse {
                print("APIClient: HTTP failure status=\(http.statusCode)")
            }
            print("API error: \(String(data: data, encoding: .utf8) ?? "")")
            return false
        } catch {
            let nsError = error as NSError
            print("APIClient: network error domain=\(nsError.domain) code=\(nsError.code) description=\(nsError.localizedDescription) url=\(baseURL.absoluteString)")
            return false
        }
    }
}
