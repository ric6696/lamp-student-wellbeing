# Metric Enum

This document defines the `metric_type` codes sent by clients.

| Metric Name   | Code (metric_type) | Recommended Sampling Rate                  |
| ------------- | ------------------ | ------------------------------------------ |
| Heart Rate    | 1                  | 1–5 minutes (Normal) / 5 seconds (Workout) |
| HRV (SDNN)    | 2                  | On change / Daily                          |
| Ambient Noise | 10                 | Every 30–60 seconds                        |
| Step Count    | 20                 | Every 15 minutes / On change               |

## Daily summary metrics

These are stored in `daily_summaries` (daily totals). Step Count can also be sent
as `metric_type` 20 when hourly aggregates are needed.

| Metric Name | Storage Field           | Recommended Sampling Rate    |
| ----------- | ----------------------- | ---------------------------- |
| Step Count  | `daily_summaries.steps` | Every 15 minutes / On change |
