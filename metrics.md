# Metric Enum

This document defines the `metric_type` codes sent by clients.

| Metric Name   | Code (metric_type) | Recommended Sampling Rate                  |
| ------------- | ------------------ | ------------------------------------------ |
| Heart Rate    | 1                  | 1–5 minutes (Normal) / 5 seconds (Workout) |
| HRV (SDNN)    | 2                  | On change / Daily                          |
| Ambient Noise | 10                 | Every 30–60 seconds                        |

## Daily summary metrics

These are stored in `daily_summaries` (not `metric_type`).

| Metric Name | Storage Field           | Recommended Sampling Rate    |
| ----------- | ----------------------- | ---------------------------- |
| Step Count  | `daily_summaries.steps` | Every 15 minutes / On change |
