import Foundation

final class LocalStore {
    static let shared = LocalStore()

    private let queue = DispatchQueue(label: "local.store.queue")
    private let fileURL: URL

    private init() {
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        self.fileURL = dir.appendingPathComponent("sensor_buffer.jsonl")
        if !FileManager.default.fileExists(atPath: fileURL.path) {
            FileManager.default.createFile(atPath: fileURL.path, contents: nil)
        }
    }

    func append(_ item: BatchItem) throws {
        try queue.sync {
            let data = try JSONCoding.encoder.encode(item)
            try appendLine(data)
        }
    }

    func append(_ items: [BatchItem]) throws {
        guard !items.isEmpty else { return }
        try queue.sync {
            for item in items {
                let data = try JSONCoding.encoder.encode(item)
                try appendLine(data)
            }
        }
    }

    func drain(limit: Int) throws -> [BatchItem] {
        try queue.sync {
            let content = try String(contentsOf: fileURL, encoding: .utf8)
            let lines = content.split(separator: "\n", omittingEmptySubsequences: true)
            guard !lines.isEmpty else { return [] }

            var items: [BatchItem] = []
            var remaining: [Substring] = []

            for (idx, line) in lines.enumerated() {
                if idx < limit {
                    if let data = line.data(using: .utf8),
                       let item = try? JSONCoding.decoder.decode(BatchItem.self, from: data) {
                        items.append(item)
                    }
                } else {
                    remaining.append(line)
                }
            }

            let remainder = remaining.map(String.init).joined(separator: "\n")
            try remainder.write(to: fileURL, atomically: true, encoding: .utf8)
            if !remainder.isEmpty {
                try appendLine(Data())
            }
            return items
        }
    }

    func count() throws -> Int {
        try queue.sync {
            let content = try String(contentsOf: fileURL, encoding: .utf8)
            return content.split(separator: "\n", omittingEmptySubsequences: true).count
        }
    }

    private func appendLine(_ data: Data) throws {
        let handle = try FileHandle(forWritingTo: fileURL)
        try handle.seekToEnd()
        if !data.isEmpty {
            try handle.write(contentsOf: data)
        }
        try handle.write(contentsOf: Data("\n".utf8))
        try handle.close()
    }

    func clear() throws {
        try queue.sync {
            try "".write(to: fileURL, atomically: true, encoding: .utf8)
        }
    }
}
