# Metric Codes

This document describes the metric codes currently accepted by the backend and defined in `metric_catalog`.

## Active codes in the current schema

| Metric Name    | Code | Unit      | Notes                                                                   |
| -------------- | ---- | --------- | ----------------------------------------------------------------------- |
| Heart Rate     | 1    | count/min | Sent from HealthKit / workout collection                                |
| Audio Exposure | 10   | dBA       | Stored in `vitals`; used by `get_session_audio_exposure_10m_stats(...)` |
| Steps          | 20   | count     | Sent as numeric vital samples                                           |
| Distance       | 21   | meter     | Walking/running distance                                                |

## Current storage

- Numeric metrics are stored in `vitals`.
- Contextual audio labels are stored separately in `audio_events` and are not identified by metric code.
- Motion labels are stored in `motion_events`.

## Notes

- `metric_code = 10` is the metric used for numeric audio exposure aggregation.
- `HRV (SDNN)` was referenced in earlier planning docs, but it is not currently seeded in `metric_catalog` in `database/init/01_schema.sql`.
- If you add a new metric code, update both `database/init/01_schema.sql` and any client collectors that emit the metric.
