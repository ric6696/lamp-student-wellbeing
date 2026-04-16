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
import psycopg2
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


def get_postgres_connection():
    return psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "sensing_db"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "dev_password"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5433"),
    )


def fetch_user_personalization_profile(cursor, user_id=None):
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = 'user_personalization_profiles'
        )
        """
    )
    if not cursor.fetchone()[0]:
        return None

    if user_id:
        cursor.execute(
            """
            SELECT user_id, profile_payload, updated_at
            FROM user_personalization_profiles
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
    else:
        cursor.execute(
            """
            SELECT user_id, profile_payload, updated_at
            FROM user_personalization_profiles
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )

    row = cursor.fetchone()
    if not row:
        return None

    profile_user_id, profile_payload, updated_at = row
    return {
        "user_id": profile_user_id,
        "profile_payload": profile_payload,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def fetch_features_from_db(session_id=None, user_id=None, device_id=None):
    conf_expr = "(CASE WHEN confidence > 1 THEN confidence / 100.0 ELSE confidence END)"

    with get_postgres_connection() as conn:
        with conn.cursor() as cursor:
            if session_id is not None:
                cursor.execute(
                    """
                    SELECT id, user_id, device_id, started_at, ended_at, label
                    FROM sessions
                    WHERE id = %s
                    """,
                    (session_id,),
                )
            else:
                filters = []
                params = []
                if user_id:
                    filters.append("user_id = %s")
                    params.append(user_id)
                if device_id:
                    filters.append("device_id = %s")
                    params.append(device_id)
                where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
                cursor.execute(
                    f"""
                    SELECT id, user_id, device_id, started_at, ended_at, label
                    FROM sessions
                    {where_clause}
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    tuple(params),
                )

            session_row = cursor.fetchone()
            if not session_row:
                raise RuntimeError("No matching session found in database")

            sid, sess_user_id, sess_device_id, started_at, ended_at, label = session_row
            if started_at is None:
                raise RuntimeError(f"Session {sid} has no started_at")
            if ended_at is None:
                raise RuntimeError(f"Session {sid} has no ended_at yet")

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM audio_events
                WHERE session_id = %s
                  AND time >= %s
                  AND time <= %s
                """,
                (sid, started_at, ended_at),
            )
            audio_events_total = int(cursor.fetchone()[0])

            cursor.execute(
                f"""
                SELECT label, db, ai_label
                FROM audio_events
                WHERE session_id = %s
                  AND time >= %s
                  AND time <= %s
                  AND confidence IS NOT NULL
                  AND {conf_expr} > 0.5
                ORDER BY time ASC
                """,
                (sid, started_at, ended_at),
            )
            qualified_audio_rows = cursor.fetchall()

            label_counts = {}
            ai_label_counts = {}
            db_values = []
            for label_val, db, ai_label in qualified_audio_rows:
                label_key = (label_val or "unknown").strip().lower()
                label_counts[label_key] = label_counts.get(label_key, 0) + 1
                if ai_label:
                    ai_key = str(ai_label).strip()
                    ai_label_counts[ai_key] = ai_label_counts.get(ai_key, 0) + 1
                dbf = to_float(db)
                if dbf is not None:
                    db_values.append(dbf)
            avg_db = sum(db_values) / len(db_values) if db_values else None

            cursor.execute(
                """
                SELECT metric_code, value
                FROM vitals
                WHERE session_id = %s
                  AND time >= %s
                  AND time <= %s
                ORDER BY time ASC
                """,
                (sid, started_at, ended_at),
            )
            vital_rows = cursor.fetchall()

            hr_values = []
            steps_values = []
            distance_values = []
            for metric_code, value in vital_rows:
                val = to_float(value)
                if val is None:
                    continue
                if metric_code == 1:
                    hr_values.append(val)
                elif metric_code == 20:
                    steps_values.append(val)
                elif metric_code == 21:
                    distance_values.append(val)

            cursor.execute(
                """
                SELECT lat, lon
                FROM gps
                WHERE session_id = %s
                  AND time >= %s
                  AND time <= %s
                  AND lat IS NOT NULL
                  AND lon IS NOT NULL
                ORDER BY time ASC
                """,
                (sid, started_at, ended_at),
            )
            gps_rows = cursor.fetchall()

            displacement_m = None
            total_travel_m = None
            if len(gps_rows) >= 2:
                start_lat, start_lon = gps_rows[0]
                end_lat, end_lon = gps_rows[-1]
                displacement_m = haversine_m(start_lat, start_lon, end_lat, end_lon)
                travel = 0.0
                for i in range(1, len(gps_rows)):
                    lat1, lon1 = gps_rows[i - 1]
                    lat2, lon2 = gps_rows[i]
                    travel += haversine_m(lat1, lon1, lat2, lon2)
                total_travel_m = travel

            drastic_location_change = bool(
                displacement_m is not None
                and total_travel_m is not None
                and (displacement_m >= 300.0 or total_travel_m >= 1000.0)
            )

            cursor.execute(
                """
                SELECT context
                FROM motion_events
                WHERE session_id = %s
                  AND time >= %s
                  AND time <= %s
                ORDER BY time ASC
                """,
                (sid, started_at, ended_at),
            )
            motion_rows = cursor.fetchall()
            motion_counts = {}
            stationary_keywords = {"stationary", "still", "sitting", "idle"}
            active_keywords = {"walking", "running", "cycling", "automotive", "moving"}
            stationary_count = 0
            active_count = 0
            for (context,) in motion_rows:
                context_key = str(context or "unknown").strip().lower()
                motion_counts[context_key] = motion_counts.get(context_key, 0) + 1
                if context_key in stationary_keywords:
                    stationary_count += 1
                elif context_key in active_keywords:
                    active_count += 1

            duration_min = max((ended_at - started_at).total_seconds() / 60.0, 0.0)
            user_profile = fetch_user_personalization_profile(cursor, user_id=sess_user_id)

            return {
                "session": {
                    "id": sid,
                    "label": label,
                    "source": "postgres_db",
                    "started_at": started_at.isoformat() if started_at else None,
                    "ended_at": ended_at.isoformat() if ended_at else None,
                    "duration_min": duration_min,
                    "user_id": sess_user_id,
                    "device_id": sess_device_id,
                },
                "counts": {
                    "audio_events_total": audio_events_total,
                    "audio_events_conf_gt_50": len(qualified_audio_rows),
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
                    "points": len(gps_rows),
                    "displacement_m": displacement_m,
                    "total_travel_m": total_travel_m,
                    "drastic_location_change": drastic_location_change,
                },
                "motion": {
                    "counts": motion_counts,
                    "counts_text": format_label_counts(motion_counts),
                    "stationary_count": stationary_count,
                    "active_count": active_count,
                },
                "user_personalization_profile": user_profile,
            }


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


def build_aggregated_sensor_features_section(features):
    counts = features["counts"]
    audio = features["audio"]
    vitals = features["vitals"]
    gps = features["gps"]
    motion = features["motion"]

    lines = [
        f"- Audio events total: {counts['audio_events_total']}",
        f"- Audio events used (confidence > 50%): {counts['audio_events_conf_gt_50']}",
    ]

    if audio.get("avg_db_conf_gt_50") is not None:
        lines.append(f"- Avg audio dB (confidence > 50% only): {audio['avg_db_conf_gt_50']}")
    if audio.get("label_counts_conf_gt_50_text") and audio["label_counts_conf_gt_50_text"] != "none":
        lines.append(f"- Audio label counts (confidence > 50% only): {audio['label_counts_conf_gt_50_text']}")
    if audio.get("ai_label_counts_conf_gt_50_text") and audio["ai_label_counts_conf_gt_50_text"] != "none":
        lines.append(f"- AI label counts (confidence > 50% only): {audio['ai_label_counts_conf_gt_50_text']}")

    if vitals.get("heart_rate_samples", 0) > 0 and vitals.get("avg_heart_rate") is not None:
        lines.append(
            f"- Avg heart rate (metric_code=1): {vitals['avg_heart_rate']} bpm from {vitals['heart_rate_samples']} samples"
        )
    if vitals.get("steps_samples", 0) > 0 and vitals.get("avg_steps") is not None:
        lines.append(f"- Avg steps (metric_code=20): {vitals['avg_steps']} from {vitals['steps_samples']} samples")
    if vitals.get("distance_samples", 0) > 0 and vitals.get("avg_distance_m") is not None:
        lines.append(
            f"- Avg distance meters (metric_code=21): {vitals['avg_distance_m']} from {vitals['distance_samples']} samples"
        )

    lines.append(f"- GPS valid points: {gps['points']}")
    if gps.get("displacement_m") is not None:
        lines.append(f"- GPS displacement (start to end): {gps['displacement_m']} meters")
        lines.append(f"- GPS total travel: {gps['total_travel_m']} meters")
        lines.append(f"- Drastic location change: {gps['drastic_location_change']}")

    if motion.get("counts_text") and motion["counts_text"] != "none":
        lines.append(f"- Motion context counts: {motion['counts_text']}")
        lines.append(f"- Stationary count: {motion['stationary_count']}, Active count: {motion['active_count']}")

    if not lines:
        lines = ["- No aggregated sensor features available"]

    return "\n".join(lines)


def format_user_personalization_profiles(profile_entry):
    if not profile_entry:
        return None
    profile_payload = profile_entry.get("profile_payload")
    if profile_payload is None:
        profile_payload = profile_entry.get("profile_json")
    if profile_payload is None:
        return None
    if isinstance(profile_payload, str):
        return profile_payload
    return json.dumps(profile_payload, indent=2, ensure_ascii=False)


def build_ccot_prompt(features):
    session = features["session"]
    aggregated_features_section = build_aggregated_sensor_features_section(features)
    user_personalization_profiles = format_user_personalization_profiles(features.get("user_personalization_profile"))

    duration_str = (
        f"{session['duration_min']:.1f} minutes"
        if isinstance(session.get("duration_min"), (int, float))
        else "unknown duration"
    )

    if user_personalization_profiles:
        return f"""You are an expert study concentration analyst. Use Compositional Chain-of-Thought (CCoT) with explicit 3-phase reasoning.

SESSION WINDOW:
- session_id: {session.get('id')}
- start: {session.get('started_at')}
- end: {session.get('ended_at')}
- duration: {duration_str}

AGGREGATED SENSOR FEATURES:
{aggregated_features_section}

USER PERSONALIZATION PROFILE:
{user_personalization_profiles}

PERSONALIZATION PRIORITY RULES:
1) Use the user's personal baseline as the PRIMARY reference when sufficient and reliable data exists.
2) Use normal concentration thresholds ONLY as fallback when:
   - a modality is missing in the personalization profile,
   - the profile is sparse or low-confidence,
   - or current readings deviate strongly from both the personal baseline and reasonable human ranges.
3) If personal baseline conflicts with generic thresholds, prefer the personal baseline UNLESS the signal clearly indicates distraction or abnormal behavior.
4) Explicitly determine whether the final assessment is based on:
   - personal baseline,
   - generic thresholds,
   - or a mix of both.

PERSONALIZATION CONFIDENCE GUIDELINES:
- High confidence: consistent historical patterns for focused sessions.
- Medium confidence: partial or somewhat variable patterns.
- Low confidence: sparse, missing, or inconsistent data.
- Lower confidence -> rely more on generic thresholds.

GENERIC BASELINE THRESHOLDS (fallback comparison only):
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
Step 1. Analyze audio relative to the user's personal baseline if available; otherwise use generic thresholds and label distribution.
Step 2. Analyze vitals (heart rate, steps, distance) relative to the user's personal baseline if available; otherwise use generic thresholds.
Step 3. Analyze GPS and motion relative to the user's typical study stability patterns if available; otherwise use generic thresholds.

PHASE 2: CROSS-MODAL COMPOSITION
Step 4. Identify correlations across modalities, noting whether patterns align or conflict with the user's personal baseline.
Step 5. Synthesize a holistic concentration assessment prioritizing personal baseline, with generic thresholds as secondary support.

PHASE 3: ACTIONABLE SYNTHESIS
Step 6. Provide concise personalized recommendations tied to root causes from the composed analysis.

SCORING RULE:
- Score primarily based on deviation from the user's personal focused-state baseline.
- Do NOT penalize differences from population averages if they align with the user's normal focused behavior.
- Penalize strong multi-modal signals of distraction (e.g., noisy + active motion + large movement).
- Use generic thresholds as secondary anchors when personalization is weak or missing for a modality.

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
  "personalization_basis": "<personal baseline | generic threshold | mixed>",
  "score": <integer 1-10>,
  "reason": "<2 to 3 sentences explaining why this score was given based on personal baseline and/or generic thresholds>"
}}

Rules:
- Score 1 means very poor concentration, 10 means excellent concentration.
- The reason MUST be exactly 2 or 3 sentences.
- The reason MUST explicitly mention whether the assessment relied on personal baseline, generic thresholds, or both.
- Mention key evidence: filtered audio dB/labels, vitals averages, gps movement, and motion context.
- No text outside the JSON object."""

    return f"""You are an expert study concentration analyst. Use Compositional Chain-of-Thought (CCoT) with explicit 3-phase reasoning.

SESSION WINDOW:
- session_id: {session.get('id')}
- start: {session.get('started_at')}
- end: {session.get('ended_at')}
- duration: {duration_str}

AGGREGATED SENSOR FEATURES:
{aggregated_features_section}

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


def analyze_concentration(features, model="claude-sonnet-4-5", session=None):
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
            # 3) Try to parse the first JSON object inside markdown fences or free text.
            raw_text = str(response_text or "")
            candidates = [raw_text]

            unescaped = raw_text
            if unescaped.startswith('"') and unescaped.endswith('"'):
                unescaped = unescaped[1:-1]
            unescaped = unescaped.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
            if unescaped != raw_text:
                candidates.append(unescaped)

            for text in candidates:
                text = text.strip()
                if text.startswith("```"):
                    lines = text.splitlines()
                    if len(lines) >= 3:
                        text = "\n".join(lines[1:-1]).strip()

                first = text.find("{")
                last = text.rfind("}")
                if first == -1 or last == -1 or first >= last:
                    continue

                snippet = text[first : last + 1]
                try:
                    parsed = json.loads(snippet)
                    break
                except json.JSONDecodeError:
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
            "personalization_basis": parsed.get("personalization_basis"),
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


def process_concentration_analysis(
    data_path=None,
    model="claude-sonnet-4-5",
    output_path="concentration_analysis_results.json",
    source="db",
    session_id=None,
    user_id=None,
    device_id=None,
):
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

    payload = None
    if source == "db":
        features = fetch_features_from_db(session_id=session_id, user_id=user_id, device_id=device_id)
        print("Loaded session sensor rows from Postgres")
    else:
        if not data_path:
            raise ValueError("--data is required when --source json")
        payload = load_payload(data_path)
        target_session = choose_target_session(payload)
        features = compute_session_features(payload, target_session)
        print(f"Loaded data from {data_path}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_target = Path(output_path)
    if not output_target.is_absolute():
        output_target = OUTPUT_DIR / output_target

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
        personalization_profile_used = bool(format_user_personalization_profiles(features.get("user_personalization_profile")))
        result = analyze_concentration(features=features, model=model, session=session)

        print("\n📋 CONCENTRATION ANALYSIS RESULTS:")
        print("=" * 100)

        if result.get("score") is not None:
            print(f"\n🎯 CONCENTRATION SCORE: {result['score']}/10")

        print("\n💭 REASON:")
        print("-" * 100)
        print(result.get("reason", "No reason provided"))
        print("=" * 100)

        strict_output = {
            "phase_1": result.get("phase_1") or {},
            "phase_2": result.get("phase_2") or {},
            "phase_3": result.get("phase_3") or {},
            "score": result.get("score"),
            "reason": result.get("reason") or "",
        }

        debug_output = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "model": model,
            "source": source,
            "session": features["session"],
            "sensor_features": features,
            "user_personalization_profile": features.get("user_personalization_profile"),
            "user_personalization_profile_used": personalization_profile_used,
            "concentration_score": result.get("score"),
            "reason": result.get("reason"),
            "phase_1": result.get("phase_1"),
            "phase_2": result.get("phase_2"),
            "phase_3": result.get("phase_3"),
            "personalization_basis": result.get("personalization_basis"),
            "raw_payload": payload,
        }

        if "error" in result:
            debug_output["error"] = result["error"]

        debug_target = output_target.with_name(output_target.stem + "_debug.json")

        with open(output_target, "w", encoding="utf-8") as f:
            json.dump(strict_output, f, indent=2, ensure_ascii=False)

        with open(debug_target, "w", encoding="utf-8") as f:
            json.dump(debug_output, f, indent=2, ensure_ascii=False)

        print("\n✓ Analysis complete!")
        print(f"✓ Results saved to: {output_target}")
        print(f"✓ Debug payload saved to: {debug_target}")

    finally:
        session.close()
        print("\n✓ Snowflake session closed")
        print("=" * 100)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Analyze study concentration using multimodal session sensors")
    parser.add_argument("--source", type=str, choices=["db", "json"], default="db", help="Read sensors from Postgres or JSON")
    parser.add_argument("--session-id", type=int, default=None, help="Optional session id to analyze (db source only)")
    parser.add_argument("--user-id", type=str, default=None, help="Optional user id filter for latest session (db source only)")
    parser.add_argument("--device-id", type=str, default=None, help="Optional device id filter for latest session (db source only)")
    parser.add_argument("--data", type=str, default=None, help="Path to input JSON (json source only)")
    parser.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-5",
        help="Model name to use (claude-sonnet-4-5, llama4-maverick, etc.)",
    )
    parser.add_argument("--output", type=str, default="concentration_analysis_results.json", help="Output filename or absolute path")

    args = parser.parse_args()

    process_concentration_analysis(
        data_path=args.data,
        model=args.model,
        output_path=args.output,
        source=args.source,
        session_id=args.session_id,
        user_id=args.user_id,
        device_id=args.device_id,
    )
