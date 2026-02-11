import Foundation

struct APIClient {
    let baseURL: URL
    let deviceId: String
    let session: URLSession = .shared

    func send(items: [BatchItem]) async -> Bool {
        guard !items.isEmpty else { return true }
        let envelope = BatchEnvelope(metadata: .init(device_id: deviceId), data: items)
        var request = URLRequest(url: baseURL)
        request.httpMethod = "POST"
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
                return true
            }
            print("API error: \(String(data: data, encoding: .utf8) ?? "")")
            return false
        } catch {
            print("Network error: \(error)")
            return false
        }
    }
}
