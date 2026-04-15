#!/usr/bin/env python3
"""
Convert the synthesized FocusLLM ingest batch into the analyzer's session-oriented JSON format.
This lets llm/CCoT/analyze_study_concentration.py run directly on the generated data.
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

repo_root = Path(__file__).resolve().parents[1]


def parse_ts(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    input_path = repo_root / "llm" / "CCoT" / "output" / "synth_focusllm_user1.json"
    output_path = repo_root / "llm" / "CCoT" / "output" / "focusllm_user1_analysis_input.json"

    if not input_path.exists():
        print(f"Missing input file: {input_path}")
        return 1

    with input_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    metadata = payload["metadata"]
    rows = payload["data"]

    grouped = defaultdict(lambda: {
        "session_key": None,
        "started_at": None,
        "ended_at": None,
        "label": "study_session",
        "audio_events": [],
        "gps": [],
        "motion_events": [],
        "vitals": [],
        "events": [],
    })

    for row in rows:
        meta = row.get("metadata") or {}
        session_key = meta.get("session_key")
        if not session_key:
            continue
        bucket = grouped[session_key]
        bucket["session_key"] = session_key

        ts = parse_ts(row["t"])
        if row["type"] == "event" and row.get("label") == "session_marker":
            marker = (row.get("val_text") or "").upper()
            if marker == "START":
                bucket["started_at"] = ts
            elif marker == "END":
                bucket["ended_at"] = ts
            bucket["events"].append(
                {
                    "time": iso_z(ts),
                    "session_id": session_key,
                    "user_id": metadata["user_id"],
                    "device_id": metadata["device_id"],
                    "label": "session_marker",
                    "val_text": row.get("val_text"),
                    "metadata": meta,
                }
            )
        elif row["type"] == "vital":
            bucket["vitals"].append(
                {
                    "time": iso_z(ts),
                    "session_id": session_key,
                    "user_id": metadata["user_id"],
                    "device_id": metadata["device_id"],
                    "metric_code": row.get("code"),
                    "value": row.get("val"),
                    "metadata": meta,
                }
            )
        elif row["type"] == "gps":
            bucket["gps"].append(
                {
                    "time": iso_z(ts),
                    "session_id": session_key,
                    "user_id": metadata["user_id"],
                    "device_id": metadata["device_id"],
                    "lat": row.get("lat"),
                    "lon": row.get("lon"),
                    "acc": row.get("acc"),
                    "metadata": meta,
                }
            )
        elif row["type"] == "event":
            label = row.get("label")
            if label == "audio_context":
                bucket["audio_events"].append(
                    {
                        "time": iso_z(ts),
                        "session_id": session_key,
                        "user_id": metadata["user_id"],
                        "device_id": metadata["device_id"],
                        "label": row.get("val_text") or "unknown",
                        "db": meta.get("db"),
                        "confidence": meta.get("confidence"),
                        "ai_label": meta.get("ai_label"),
                        "ai_confidence": meta.get("ai_confidence"),
                        "metadata": meta,
                    }
                )
            elif label == "motion_context":
                bucket["motion_events"].append(
                    {
                        "time": iso_z(ts),
                        "session_id": session_key,
                        "user_id": metadata["user_id"],
                        "device_id": metadata["device_id"],
                        "context": row.get("val_text") or "unknown",
                        "metadata": meta,
                    }
                )
            else:
                bucket["events"].append(
                    {
                        "time": iso_z(ts),
                        "session_id": session_key,
                        "user_id": metadata["user_id"],
                        "device_id": metadata["device_id"],
                        "label": label,
                        "val_text": row.get("val_text"),
                        "metadata": meta,
                    }
                )

    sessions = []
    audio_events = []
    gps = []
    motion_events = []
    vitals = []
    events = []

    for i, session_key in enumerate(sorted(grouped.keys()), 1):
        bucket = grouped[session_key]
        started_at = bucket["started_at"]
        ended_at = bucket["ended_at"]
        if started_at is None or ended_at is None:
            continue
        sessions.append(
            {
                "id": session_key,
                "user_id": metadata["user_id"],
                "device_id": metadata["device_id"],
                "started_at": iso_z(started_at),
                "ended_at": iso_z(ended_at),
                "label": bucket["label"],
            }
        )
        audio_events.extend(bucket["audio_events"])
        gps.extend(bucket["gps"])
        motion_events.extend(bucket["motion_events"])
        vitals.extend(bucket["vitals"])
        events.extend(bucket["events"])

    output = {
        "sessions": sessions,
        "audio_events": audio_events,
        "gps": gps,
        "motion_events": motion_events,
        "vitals": vitals,
        "events": events,
    }

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Wrote {output_path}")
    print(f"Sessions: {len(sessions)}")
    print(f"Audio events: {len(audio_events)}")
    print(f"GPS rows: {len(gps)}")
    print(f"Motion rows: {len(motion_events)}")
    print(f"Vitals rows: {len(vitals)}")
    print(f"Other events: {len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
