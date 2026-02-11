# iOS Integration Guide

This guide defines the request payload, Swift models, and a curl test for the `/ingest` endpoint.

## JSON Sample

```json
{
  "metadata": {
    "device_id": "11111111-1111-1111-1111-111111111111",
    "version": "1.0",
    "model_name": "iPhone 15 Pro"
  },
  "data": [
    {
      "t": "2026-02-10T14:57:07Z",
      "type": "gps",
      "lat": 34.0522,
      "lon": -118.2437,
      "acc": 5.0
    },
    {
      "t": "2026-02-10T14:57:02Z",
      "type": "vital",
      "code": 1,
      "val": 72
    },
    {
      "t": "2026-02-10T14:56:57Z",
      "type": "event",
      "label": "motion_state",
      "val_text": "walking"
    }
  ]
}
```

## Swift Structs

```swift
import Foundation

struct Batch: Codable {
    let metadata: Metadata
    let data: [Reading]
}

struct Metadata: Codable {
    let deviceId: String
    let version: String?
    let userId: String?
    let modelName: String?

    enum CodingKeys: String, CodingKey {
        case deviceId = "device_id"
        case version
        case userId = "user_id"
        case modelName = "model_name"
    }
}

enum Reading: Codable {
    case vital(VitalReading)
    case gps(GpsReading)
    case event(EventReading)

    enum CodingKeys: String, CodingKey {
        case type
    }

    enum ReadingType: String, Codable {
        case vital
        case gps
        case event
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(ReadingType.self, forKey: .type)
        switch type {
        case .vital:
            self = .vital(try VitalReading(from: decoder))
        case .gps:
            self = .gps(try GpsReading(from: decoder))
        case .event:
            self = .event(try EventReading(from: decoder))
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .vital(let reading):
            try reading.encode(to: encoder)
        case .gps(let reading):
            try reading.encode(to: encoder)
        case .event(let reading):
            try reading.encode(to: encoder)
        }
    }
}

struct VitalReading: Codable {
    let type: String
    let t: String
    let code: Int
    let val: Double
}

struct GpsReading: Codable {
    let type: String
    let t: String
    let lat: Double
    let lon: Double
    let acc: Double?
}

struct EventReading: Codable {
    let type: String
    let t: String
    let label: String
    let valText: String?
    let metadata: [String: String]?

    enum CodingKeys: String, CodingKey {
        case type
        case t
        case label
        case valText = "val_text"
        case metadata
    }
}
```

## curl Test

Replace the base URL and API key as needed.

```bash
curl -X POST https://<your-ngrok-domain>.ngrok-free.app/ingest \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <INGEST_API_KEY>" \
  -d '{"metadata":{"device_id":"11111111-1111-1111-1111-111111111111","version":"1.0","model_name":"iPhone 15 Pro"},"data":[{"t":"2026-02-10T14:57:07Z","type":"gps","lat":34.0522,"lon":-118.2437,"acc":5.0},{"t":"2026-02-10T14:57:02Z","type":"vital","code":1,"val":72},{"t":"2026-02-10T14:56:57Z","type":"event","label":"motion_state","val_text":"walking"}]}'
```
