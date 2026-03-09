import Foundation
import Security
import UIKit

enum JSONValue: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let boolValue = try? container.decode(Bool.self) {
            self = .bool(boolValue)
        } else if let numberValue = try? container.decode(Double.self) {
            self = .number(numberValue)
        } else if let stringValue = try? container.decode(String.self) {
            self = .string(stringValue)
        } else {
            throw DecodingError.typeMismatch(
                JSONValue.self,
                .init(codingPath: decoder.codingPath, debugDescription: "Unsupported JSON metadata value")
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    var stringValue: String? {
        if case .string(let value) = self {
            return value
        }
        return nil
    }

    var displayText: String {
        switch self {
        case .string(let value):
            return value
        case .number(let value):
            if value.rounded() == value {
                return String(Int(value))
            }
            return String(format: "%.2f", value)
        case .bool(let value):
            return value ? "true" : "false"
        case .null:
            return "null"
        }
    }
}

enum SampleType: String, Codable {
    case vital
    case gps
    case event
}

struct BatchEnvelope: Codable {
    struct Metadata: Codable {
        let device_id: String
        let user_id: String
        let model_name: String?
    }
    let metadata: Metadata
    var data: [BatchItem]
}

struct BatchItem: Codable {
    var device_id: String?
    var type: SampleType
    var t: Date
    // vitals
    var code: Int?
    var val: Double?
    // gps
    var lat: Double?
    var lon: Double?
    var acc: Double? 
    var motion_context: String?
    // events
    var label: String?
    var val_text: String?
    var metadata: [String: JSONValue]?

    init(
        device_id: String? = nil,
        type: SampleType,
        t: Date,
        code: Int? = nil,
        val: Double? = nil,
        lat: Double? = nil,
        lon: Double? = nil,
        acc: Double? = nil,
        motion_context: String? = nil,
        label: String? = nil,
        val_text: String? = nil,
        metadata: [String: JSONValue]? = nil
    ) {
        self.device_id = device_id
        self.type = type
        self.t = t
        self.code = code
        self.val = val
        self.lat = lat
        self.lon = lon
        self.acc = acc
        self.motion_context = motion_context
        self.label = label
        self.val_text = val_text
        self.metadata = metadata
    }
}

struct DailySummary: Codable {
    let date: Date
    let device_id: String
    let steps: Int
    let active_energy_kcal: Double
    let exercise_min: Int
    let sleep_start: Date?
    let sleep_end: Date?
}

struct DailyAggregates {
    let summary: DailySummary
    let standMinutes: Int
}

enum MotionContext: String, Codable {
    case stationary
    case walking
    case running
    case cycling
    case driving
    case unknown
}

enum AppIdentity {
    private static let defaults = UserDefaults.standard
    private static let userIdKey = "lamp.identity.user_id"
    private static let phoneDeviceIdKey = "lamp.identity.phone_device_id"

    static var userId: String {
        if let persisted = KeychainValueStore.string(for: userIdKey) {
            return persisted
        }

        if let migrated = defaults.string(forKey: userIdKey), !migrated.isEmpty {
            KeychainValueStore.set(migrated, for: userIdKey)
            return migrated
        }

        let created = "user-\(UUID().uuidString.lowercased())"
        KeychainValueStore.set(created, for: userIdKey)
        return created
    }

    static var phoneDeviceId: String {
        persistedIdentifier(forKey: phoneDeviceIdKey, prefix: "phone")
    }

    private static func persistedIdentifier(forKey key: String, prefix: String) -> String {
        if let existing = defaults.string(forKey: key), !existing.isEmpty {
            return existing
        }
        let created = "\(prefix)-\(UUID().uuidString.lowercased())"
        defaults.set(created, forKey: key)
        return created
    }
}

private enum KeychainValueStore {
    private static let service = "com.lamp.wellbeing.identity"

    static func string(for account: String) -> String? {
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecReturnData: true,
            kSecMatchLimit: kSecMatchLimitOne,
        ]

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8),
              !value.isEmpty else {
            return nil
        }

        return value
    }

    static func set(_ value: String, for account: String) {
        let encoded = Data(value.utf8)
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
        ]
        let attributes: [CFString: Any] = [
            kSecValueData: encoded,
        ]

        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess {
            return
        }

        var createQuery = query
        createQuery[kSecValueData] = encoded
        SecItemAdd(createQuery as CFDictionary, nil)
    }
}

struct DeviceId {
    static var value: String {
        AppIdentity.phoneDeviceId
    }
}

enum StudySessionContext {
    private static let queue = DispatchQueue(label: "study.session.context")
    private static var activeSessionKey: String?

    static func startNewSession() -> String {
        let created = "study-session-\(UUID().uuidString.lowercased())"
        queue.sync {
            activeSessionKey = created
        }
        return created
    }

    static func currentSessionKey() -> String? {
        queue.sync { activeSessionKey }
    }

    static func clear() {
        queue.sync {
            activeSessionKey = nil
        }
    }

    static func stamp(metadata: [String: JSONValue]?) -> [String: JSONValue]? {
        guard let sessionKey = currentSessionKey() else { return metadata }
        var stamped = metadata ?? [:]
        stamped["session_key"] = .string(sessionKey)
        return stamped
    }

    static func stamp(item: BatchItem) -> BatchItem {
        guard currentSessionKey() != nil else { return item }
        var stamped = item
        stamped.metadata = stamp(metadata: item.metadata)
        return stamped
    }
}
