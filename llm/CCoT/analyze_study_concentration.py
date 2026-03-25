"""
Analyze study concentration using multimodal sensor data with Compositional Chain-of-Thought (CCoT).

Expected preferred input JSON shape:
{
  "sessions": [...],
  "audio_events": [...],
  "gps": [...],
  "motion_events": [...],
  "vitals": [...]
}

If sessions are missing, the script falls back to one inferred session using min/max timestamps.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from snowflake.snowpark import Session


OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def parse_ts(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_confidence(value):
    conf = to_float(value)
    if conf is None:
        return None
    # Accept either 0-1 or 0-100 scale.
    if conf > 1.0:
        conf = conf / 100.0
    return conf


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def row_time_in_session(row, session_start, session_end):
    t = parse_ts(row.get("time") or row.get("t"))
    if not t or not session_start:
        return False
    if session_end:
        return session_start <= t <= session_end
    return t >= session_start


def choose_target_session(payload):
    sessions = payload.get("sessions") or []
    if sessions:
        def session_key(s):
            return parse_ts(s.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc)

        latest = sorted(sessions, key=session_key)[-1]
        start = parse_ts(latest.get("started_at"))
        end = parse_ts(latest.get("ended_at"))
        return {
            "id": latest.get("id"),
            "user_id": latest.get("user_id"),
            "device_id": latest.get("device_id"),
            "started_at": start,
            "ended_at": end,
            "label": latest.get("label"),
            "source": "sessions_table",
        }

    # Fallback: infer one session from all known rows.
    all_rows = []
    for key in ("audio_events", "gps", "motion_events", "vitals", "events"):
        all_rows.extend(payload.get(key) or [])
    times = [parse_ts(r.get("time") or r.get("t")) for r in all_rows]
    times = [t for t in times if t is not None]
    if not times:
        return {
            "id": None,
            "user_id": None,
            "device_id": None,
            "started_at": None,
            "ended_at": None,
            "label": None,
            "source": "inferred_empty",
        }

    return {
        "id": None,
        "user_id": None,
        "device_id": None,
        "started_at": min(times),
        "ended_at": max(times),
        "label": "inferred_session",
        "source": "inferred_time_window",
    }


def row_matches_session(row, session_meta):
    sid = session_meta.get("id")
    row_sid = row.get("session_id")
    user_id = session_meta.get("user_id")
    device_id = session_meta.get("device_id")

    # Prefer explicit session_id match when available.
    if sid is not None and row_sid is not None:
        return str(sid) == str(row_sid)

    # Fall back to time window + optional identity match.
    start = session_meta.get("started_at")
    end = session_meta.get("ended_at")
    if not row_time_in_session(row, start, end):
        return False

    if user_id and row.get("user_id") and str(user_id) != str(row.get("user_id")):
        return False
    if device_id and row.get("device_id") and str(device_id) != str(row.get("device_id")):
        return False
    return True


def format_label_counts(counts):
    if not counts:
        return "none"
    items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return ", ".join(f"{count} {label}" for label, count in items)


def compute_session_features(payload, session_meta):
    audio_rows = [r for r in (payload.get("audio_events") or []) if row_matches_session(r, session_meta)]
    gps_rows = [r for r in (payload.get("gps") or []) if row_matches_session(r, session_meta)]
    motion_rows = [r for r in (payload.get("motion_events") or []) if row_matches_session(r, session_meta)]
    vital_rows = [r for r in (payload.get("vitals") or []) if row_matches_session(r, session_meta)]

    # Audio: keep only confidence > 50%.
    qualified_audio = []
    label_counts = {}
    ai_label_counts = {}
    db_values = []
    for row in audio_rows:
        conf = normalize_confidence(row.get("confidence"))
        if conf is None or conf <= 0.5:
            continue
        qualified_audio.append(row)
        label = (row.get("label") or "unknown").strip().lower()
        label_counts[label] = label_counts.get(label, 0) + 1

        ai_label = row.get("ai_label")
        if ai_label:
            ai_key = str(ai_label).strip()
            ai_label_counts[ai_key] = ai_label_counts.get(ai_key, 0) + 1

        db = to_float(row.get("db"))
        if db is not None:
            db_values.append(db)

    avg_db = sum(db_values) / len(db_values) if db_values else None

    # Vitals for heart rate, steps, distance.
    hr_values = []
    steps_values = []
    distance_values = []
    for row in vital_rows:
        code = row.get("metric_code")
        val = to_float(row.get("value"))
        if val is None:
            continue
        try:
            code = int(code)
        except (TypeError, ValueError):
            continue

        if code == 1:
            hr_values.append(val)
        elif code == 20:
            steps_values.append(val)
        elif code == 21:
            distance_values.append(val)

    # GPS displacement and travel.
    valid_gps = []
    for row in gps_rows:
        lat = to_float(row.get("lat"))
        lon = to_float(row.get("lon"))
        t = parse_ts(row.get("time") or row.get("t"))
        if lat is None or lon is None or t is None:
            continue
        valid_gps.append((t, lat, lon))
    valid_gps.sort(key=lambda x: x[0])

    displacement_m = None
    total_travel_m = 0.0
    if len(valid_gps) >= 2:
        start = valid_gps[0]
        end = valid_gps[-1]
        displacement_m = haversine_m(start[1], start[2], end[1], end[2])
        for i in range(1, len(valid_gps)):
            total_travel_m += haversine_m(
                valid_gps[i - 1][1],
                valid_gps[i - 1][2],
                valid_gps[i][1],
                valid_gps[i][2],
            )

    drastic_location_change = (
        displacement_m is not None and (displacement_m >= 300.0 or total_travel_m >= 1000.0)
    )

    # Motion distribution.
    motion_counts = {}
    stationary_keywords = {"stationary", "still", "sitting", "idle"}
    active_keywords = {"walking", "running", "cycling", "automotive", "moving"}
    stationary_count = 0
    active_count = 0
    for row in motion_rows:
        context = str(row.get("context") or "unknown").strip().lower()
        motion_counts[context] = motion_counts.get(context, 0) + 1
        if context in stationary_keywords:
            stationary_count += 1
        elif context in active_keywords:
            active_count += 1

    duration_min = None
    if session_meta.get("started_at") and session_meta.get("ended_at"):
        delta = session_meta["ended_at"] - session_meta["started_at"]
        duration_min = max(delta.total_seconds() / 60.0, 0.0)

    return {
        "session": {
            "id": session_meta.get("id"),
            "label": session_meta.get("label"),
            "source": session_meta.get("source"),
            "started_at": session_meta.get("started_at").isoformat() if session_meta.get("started_at") else None,
            "ended_at": session_meta.get("ended_at").isoformat() if session_meta.get("ended_at") else None,
            "duration_min": duration_min,
            "user_id": session_meta.get("user_id"),
            "device_id": session_meta.get("device_id"),
        },
        "counts": {
            "audio_events_total": len(audio_rows),
            "audio_events_conf_gt_50": len(qualified_audio),
            "gps_points": len(gps_rows),
            "motion_events": len(motion_rows),
            "vitals_rows": len(vital_rows),
        },
        "audio": {
            "avg_db_conf_gt_50": avg_db,
            "label_counts_conf_gt_50": label_counts,
            "label_counts_conf_gt_50_text": format_label_counts(label_counts),
            "ai_label_counts_conf_gt_50": ai_label_counts,
            "ai_label_counts_conf_gt_50_text": format_label_counts(ai_label_counts),
        },
        "vitals": {
            "avg_heart_rate": (sum(hr_values) / len(hr_values)) if hr_values else None,
            "avg_steps": (sum(steps_values) / len(steps_values)) if steps_values else None,
            "avg_distance_m": (sum(distance_values) / len(distance_values)) if distance_values else None,
            "heart_rate_samples": len(hr_values),
            "steps_samples": len(steps_values),
            "distance_samples": len(distance_values),
        },
        "gps": {
            "points": len(valid_gps),
            "displacement_m": displacement_m,
            "total_travel_m": total_travel_m if valid_gps else None,
            "drastic_location_change": drastic_location_change,
        },
        "motion": {
            "counts": motion_counts,
            "counts_text": format_label_counts(motion_counts),
            "stationary_count": stationary_count,
            "active_count": active_count,
        },
    }


def build_ccot_prompt(features):
    session = features["session"]
    counts = features["counts"]
    audio = features["audio"]
    vitals = features["vitals"]
    gps = features["gps"]
    motion = features["motion"]

    duration_str = (
        f"{session['duration_min']:.1f} minutes"
        if isinstance(session.get("duration_min"), (int, float))
        else "unknown duration"
    )

    return f"""You are an expert study concentration analyst. Use Compositional Chain-of-Thought (CCoT) with explicit 3-phase reasoning.

SESSION WINDOW:
- session_id: {session.get('id')}
- start: {session.get('started_at')}
- end: {session.get('ended_at')}
- duration: {duration_str}

AGGREGATED SENSOR FEATURES:
- Audio events total: {counts['audio_events_total']}
- Audio events used (confidence > 50%): {counts['audio_events_conf_gt_50']}
- Avg audio dB (confidence > 50% only): {audio['avg_db_conf_gt_50']}
- Audio label counts (confidence > 50% only): {audio['label_counts_conf_gt_50_text']}
- AI label counts (confidence > 50% only): {audio['ai_label_counts_conf_gt_50_text']}
- Avg heart rate (metric_code=1): {vitals['avg_heart_rate']} bpm from {vitals['heart_rate_samples']} samples
- Avg steps (metric_code=20): {vitals['avg_steps']} from {vitals['steps_samples']} samples
- Avg distance meters (metric_code=21): {vitals['avg_distance_m']} from {vitals['distance_samples']} samples
- GPS valid points: {gps['points']}
- GPS displacement (start to end): {gps['displacement_m']} meters
- GPS total travel: {gps['total_travel_m']} meters
- Drastic location change: {gps['drastic_location_change']}
- Motion context counts: {motion['counts_text']}
- Stationary count: {motion['stationary_count']}, Active count: {motion['active_count']}

NORMAL CONCENTRATION THRESHOLDS (for comparison):
1) Audio environment (confidence-filtered):
   - Good focus: avg dB <= 45
   - Acceptable: 45 < avg dB <= 60
   - Distracting: avg dB > 60
   - Label clues: more quiet/stationary labels support concentration; more busy/speech/traffic labels reduce concentration.

2) Physiology and activity:
   - Heart rate: 60-85 bpm generally supports calm focus.
   - Steps and distance: lower movement usually indicates seated study; high values suggest movement interrupting focus.

3) Location stability:
   - Stable study location: displacement < 200m and low total travel.
   - Moderate movement: 200-500m.
   - Drastic movement: >500m displacement or high travel distance.

4) Motion context:
   - Mostly stationary contexts support concentration.
   - Frequent walking/running/active contexts suggest reduced concentration.

PHASE 1: INDIVIDUAL MODALITY DECOMPOSITION
Step 1. Analyze audio independently against thresholds and label distribution.
Step 2. Analyze vitals independently (heart rate, steps, distance) against thresholds.
Step 3. Analyze GPS and motion independently for stability vs activity.

PHASE 2: CROSS-MODAL COMPOSITION
Step 4. Identify correlations across modalities (e.g., noisy + active motion + movement in location).
Step 5. Synthesize a holistic concentration assessment from all modalities.

PHASE 3: ACTIONABLE SYNTHESIS
Step 6. Provide concise personalized recommendations tied to root causes from the composed analysis.

Return ONLY valid JSON in this exact structure:
{{
  "phase_1": {{
    "audio": "<brief modality-specific analysis>",
    "vitals": "<brief modality-specific analysis>",
    "gps_motion": "<brief modality-specific analysis>"
  }},
  "phase_2": {{
    "correlations": "<cross-modal correlations>",
    "holistic_assessment": "<overall concentration assessment>"
  }},
  "phase_3": {{
    "recommendations": "<2-3 actionable recommendations>"
  }},
  "score": <integer 1-10>,
  "reason": "<2 to 3 sentences explaining why this score was given based on the sensors and thresholds>"
}}

Rules:
- Score 1 means very poor concentration, 10 means excellent concentration.
- The reason MUST be exactly 2 or 3 sentences.
- Mention key evidence: filtered audio dB/labels, vitals averages, gps movement, and motion context.
- No text outside the JSON object."""


def analyze_concentration(features, model="claude-3-5-sonnet", session=None):
    """
    Use LLM with CCoT to analyze concentration from aggregated multimodal features.

    Returns:
        Dict containing score and reason.
    """
    prompt = build_ccot_prompt(features)
    escaped_prompt = prompt.replace("'", "''")

    query = f"""
    SELECT SNOWFLAKE.CORTEX.COMPLETE(
        '{model}',
        '{escaped_prompt}'
    ) AS response
    """

    try:
        result = session.sql(query).collect()
        if not result:
            return {"score": None, "reason": "[ERROR] No response from model"}

        response_text = result[0]["RESPONSE"]
        parsed = None

        # 1) Direct parse
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # 2) Attempt unescape wrapper string
        if parsed is None:
            try:
                text = response_text
                if text.startswith('"') and text.endswith('"'):
                    text = text[1:-1]
                text = text.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError, AttributeError):
                parsed = None

        if parsed is None:
            return {
                "score": None,
                "reason": response_text,
                "error": "Response was not valid JSON",
            }

        score = parsed.get("score")
        reason = parsed.get("reason") or parsed.get("reasoning")
        return {
            "phase_1": parsed.get("phase_1"),
            "phase_2": parsed.get("phase_2"),
            "phase_3": parsed.get("phase_3"),
            "score": score,
            "reason": reason,
        }
    except Exception as e:
        return {"score": None, "reason": f"[ERROR] {str(e)}"}


def load_payload(data_path):
    with open(data_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # Backward compatibility: old format was a flat list of readings.
    if isinstance(payload, list):
        converted = {
            "sessions": [],
            "audio_events": [],
            "gps": [],
            "motion_events": [],
            "vitals": [],
            "events": [],
        }
        for row in payload:
            sensor_type = row.get("sensor_type")
            ts = row.get("timestamp")
            value = row.get("value")
            if sensor_type == "heart_rate":
                converted["vitals"].append(
                    {"time": ts, "metric_code": 1, "value": value, "device_id": row.get("device_id"), "user_id": row.get("user_id")}
                )
            elif sensor_type == "noise_level":
                converted["audio_events"].append(
                    {
                        "time": ts,
                        "label": "noise_level",
                        "db": value,
                        "confidence": 1.0,
                        "device_id": row.get("device_id"),
                        "user_id": row.get("user_id"),
                    }
                )
            elif sensor_type == "number_of_steps_past_minute":
                converted["vitals"].append(
                    {"time": ts, "metric_code": 20, "value": value, "device_id": row.get("device_id"), "user_id": row.get("user_id")}
                )
        return converted

    if isinstance(payload, dict):
        return payload

    raise ValueError("Unsupported input JSON format")


def process_concentration_analysis(data_path, model="claude-3-5-sonnet", output_path="concentration_analysis_results.json"):
    """
    Process multimodal sensor data and analyze study concentration for the latest session.
    """
    load_dotenv()

    connection_params = {
        "account": os.environ.get("SNOWFLAKE_ACCOUNT"),
        "user": os.environ.get("SNOWFLAKE_USER"),
        "password": os.environ.get("SNOWFLAKE_USER_PASSWORD"),
        "role": os.environ.get("SNOWFLAKE_ROLE"),
        "database": os.environ.get("SNOWFLAKE_DATABASE"),
        "schema": os.environ.get("SNOWFLAKE_SCHEMA"),
        "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE"),
    }

    print("=" * 100)
    print("Study Concentration Analysis - Multi-Sensor Session CCoT")
    print("=" * 100)
    print("\nConnecting to Snowflake...")
    session = Session.builder.configs(connection_params).create()
    print("✓ Connected to Snowflake\n")

    payload = load_payload(data_path)
    target_session = choose_target_session(payload)
    features = compute_session_features(payload, target_session)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_target = Path(output_path)
    if not output_target.is_absolute():
        output_target = OUTPUT_DIR / output_target

    print(f"Loaded data from {data_path}")
    print("\n📊 Session Summary:")
    print(f"   Session ID: {features['session']['id']}")
    print(f"   Start: {features['session']['started_at']}")
    print(f"   End: {features['session']['ended_at']}")
    print(f"   Duration (min): {features['session']['duration_min']}")
    print(f"   Audio events used (>50% conf): {features['counts']['audio_events_conf_gt_50']}")
    print(f"   Avg audio dB: {features['audio']['avg_db_conf_gt_50']}")
    print(f"   Audio labels: {features['audio']['label_counts_conf_gt_50_text']}")
    print(f"   Avg heart rate: {features['vitals']['avg_heart_rate']}")
    print(f"   Avg steps: {features['vitals']['avg_steps']}")
    print(f"   Avg distance (m): {features['vitals']['avg_distance_m']}")
    print(f"   GPS drastic location change: {features['gps']['drastic_location_change']}")
    print(f"   Motion contexts: {features['motion']['counts_text']}")
    print()

    print(f"🤖 Analyzing concentration with model: {model}")
    print("=" * 100)

    try:
        result = analyze_concentration(features=features, model=model, session=session)

        print("\n📋 CONCENTRATION ANALYSIS RESULTS:")
        print("=" * 100)

        if result.get("score") is not None:
            print(f"\n🎯 CONCENTRATION SCORE: {result['score']}/10")

        print("\n💭 REASON:")
        print("-" * 100)
        print(result.get("reason", "No reason provided"))
        print("=" * 100)

        output_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "model": model,
            "session": features["session"],
            "sensor_features": features,
            "concentration_score": result.get("score"),
            "reason": result.get("reason"),
            "phase_1": result.get("phase_1"),
            "phase_2": result.get("phase_2"),
            "phase_3": result.get("phase_3"),
            "raw_payload": payload,
        }

        if "error" in result:
            output_data["error"] = result["error"]

        with open(output_target, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print("\n✓ Analysis complete!")
        print(f"✓ Results saved to: {output_target}")

    finally:
        session.close()
        print("\n✓ Snowflake session closed")
        print("=" * 100)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Analyze study concentration using multimodal session sensors")
    parser.add_argument("--data", type=str, default="fyp_test.json", help="Path to input JSON")
    parser.add_argument(
        "--model",
        type=str,
        default="claude-3-5-sonnet",
        help="Model name to use (claude-3-5-sonnet, llama4-maverick, etc.)",
    )
    parser.add_argument("--output", type=str, default="concentration_analysis_results.json", help="Output filename or absolute path")

    args = parser.parse_args()

    process_concentration_analysis(
        data_path=args.data,
        model=args.model,
        output_path=args.output,
    )
