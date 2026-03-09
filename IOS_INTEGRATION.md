# iOS Integration Guide

This guide documents the current request contract used by `ios/WellbeingApp` when posting to `/ingest`.

## Current Client Behavior

The live iOS client currently:

- sends `metadata.device_id`
- sends `metadata.user_id`
- generates a stable random `user_id` on first launch and persists it in Keychain
- uploads phone-originated and watch-originated samples under different `device_id` values within the same ingest batch when needed
- posts to the URL in `ios/WellbeingApp/Resources/Info.plist` under `APIBaseURL`
- sends `X-API-Key: dev_key` from `ios/WellbeingApp/Sources/APIClient.swift`
- emits `session_marker` events for session start and end
- emits `audio_context` events with classifier metadata
- stores numeric audio exposure as `vital` with `code = 10`

## JSON Example

```json
{
  "metadata": {
    "user_id": "study-user-001",
    "device_id": "11111111-1111-1111-1111-111111111111",
    "version": "1.0",
    "model_name": "iPhone 15 Pro"
  },
  "data": [
    {
      "t": "2026-03-06T04:19:06Z",
      "type": "event",
      "label": "session_marker",
      "val_text": "START"
    },
    {
      "t": "2026-03-06T04:19:08Z",
      "type": "gps",
      "lat": 22.3193,
      "lon": 114.1694,
      "acc": 8.0,
      "motion_context": "walking",
      "metadata": {
        "source": "core_location"
      }
    },
    {
      "t": "2026-03-06T04:19:10Z",
      "type": "vital",
      "code": 10,
      "val": 63.2
    },
    {
      "t": "2026-03-06T04:19:12Z",
      "type": "event",
      "label": "audio_context",
      "val_text": "busy",
      "metadata": {
        "db": "-37.20",
        "confidence": "0.71",
        "label_source": "sound_analysis",
        "heuristic_label": "busy",
        "ai_label": "Speech",
        "ai_confidence": "0.66"
      }
    },
    {
      "t": "2026-03-06T04:29:22Z",
      "type": "event",
      "label": "session_marker",
      "val_text": "END"
    }
  ]
}
```

## Backend Mapping

Current server-side routing in `backend/app/ingest.py` writes to:

- `vital` -> `vitals`
- `gps` -> `gps`
- `event(label=motion_context)` -> `motion_events`
- `event(label=audio_context)` -> `audio_events`
- all other events -> `events`
- `event(label=session_marker, val_text=START|END)` -> `sessions` plus backfilled `session_id`

## Swift Models In Repo

The active Swift request types live in:

- `ios/WellbeingApp/Sources/Models.swift`
- `ios/WellbeingApp/Sources/APIClient.swift`

Important notes:

- `BatchEnvelope.Metadata` now encodes both `user_id` and `device_id`.
- `user_id` is generated once on-device, stored in Keychain, and then reused across uploads.
- The phone still performs the upload, and watch-originated samples keep their own per-reading `device_id` inside the posted batch.

## curl Test

Replace the host with your local machine IP or ngrok URL.

```bash
curl -X POST http://<your-host>:8000/ingest \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev_key" \
  -d '{
    "metadata": {
      "user_id": "study-user-001",
      "device_id": "11111111-1111-1111-1111-111111111111",
      "version": "1.0",
      "model_name": "iPhone 15 Pro"
    },
    "data": [
      {
        "t": "2026-03-06T04:19:06Z",
        "type": "event",
        "label": "session_marker",
        "val_text": "START"
      },
      {
        "t": "2026-03-06T04:19:10Z",
        "type": "vital",
        "code": 10,
        "val": 63.2
      },
      {
        "t": "2026-03-06T04:19:12Z",
        "type": "event",
        "label": "audio_context",
        "val_text": "busy",
        "metadata": {
          "db": "-37.20",
          "confidence": "0.71"
        }
      },
      {
        "t": "2026-03-06T04:29:22Z",
        "type": "event",
        "label": "session_marker",
        "val_text": "END"
      }
    ]
  }'
```

Expected response:

```json
{ "status": "accepted", "records": 4 }
```

## Post-Request SQL Checks

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT id, device_id, started_at, ended_at FROM sessions ORDER BY started_at DESC LIMIT 5;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, metric_code, value, session_id FROM vitals WHERE device_id = '11111111-1111-1111-1111-111111111111' ORDER BY time DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, label, db, confidence, ai_label, session_id FROM audio_events WHERE device_id = '11111111-1111-1111-1111-111111111111' ORDER BY time DESC LIMIT 10;"
unset PGPASSWORD
```
