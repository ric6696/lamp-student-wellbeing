import Foundation
import UIKit

enum SampleType: String, Codable {
    case vital
    case gps
    case event
}

struct BatchEnvelope: Codable {
    struct Metadata: Codable { let device_id: String }
    let metadata: Metadata
    var data: [BatchItem]
}

struct BatchItem: Codable {
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
    var metadata: [String: String]?

    init(
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
        metadata: [String: String]? = nil
    ) {
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

struct DeviceId {
    static var value: String {
        UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString
    }
}
