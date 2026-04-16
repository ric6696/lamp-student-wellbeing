import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Optional
from urllib import error, request

from .config import settings
from .db import get_connection, release_connection


_repo_root = Path(__file__).resolve().parents[2]
_log_dir = _repo_root / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_ccot_output_dir = _repo_root / "llm" / "CCoT" / "output"
_ccot_output_path = _ccot_output_dir / "concentration_analysis_results.json"
_pre_session_context_path = _ccot_output_dir / "pre_session_context.json"
_legacy_output_paths = [
    _ccot_output_dir / "concentration_result.json",
]

logger = logging.getLogger("concentration")
if not logger.handlers:
    handler = logging.FileHandler(_log_dir / "concentration_worker.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def ensure_concentration_schema(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS session_concentration_analysis (
            session_id BIGINT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            score SMALLINT,
            reason TEXT,
            model TEXT,
            sensor_features JSONB,
            llm_raw_response TEXT,
            error_message TEXT,
            triggered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            processing_started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS session_concentration_analysis_status_triggered_idx
            ON session_concentration_analysis (status, triggered_at)
        """
    )


def queue_session_analysis(cursor, session_id: int, user_id: str, device_id: str) -> None:
    ensure_concentration_schema(cursor)
    cursor.execute(
        """
        INSERT INTO session_concentration_analysis (session_id, user_id, device_id, status, triggered_at, updated_at)
        VALUES (%s, %s, %s, 'pending', now(), now())
        ON CONFLICT (session_id)
        DO UPDATE SET
            user_id = EXCLUDED.user_id,
            device_id = EXCLUDED.device_id,
            status = CASE
                WHEN session_concentration_analysis.status = 'done' THEN session_concentration_analysis.status
                ELSE 'pending'
            END,
            error_message = NULL,
            updated_at = now()
        """,
        (session_id, user_id, device_id),
    )


@dataclass
class AnalysisJob:
    session_id: int
    user_id: str
    device_id: str


def _claim_next_job(cursor) -> Optional[AnalysisJob]:
    cursor.execute(
        """
        WITH candidate AS (
            SELECT session_id
            FROM session_concentration_analysis
            WHERE status = 'pending'
            ORDER BY triggered_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE session_concentration_analysis a
        SET status = 'processing',
            processing_started_at = now(),
            updated_at = now()
        FROM candidate
        WHERE a.session_id = candidate.session_id
        RETURNING a.session_id, a.user_id, a.device_id
        """
    )
    row = cursor.fetchone()
    if not row:
        return None
    return AnalysisJob(session_id=row[0], user_id=row[1], device_id=row[2])


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_confidence(value: Any) -> Optional[float]:
    conf = _to_float(value)
    if conf is None:
        return None
    if conf > 1:
        conf = conf / 100.0
    return conf


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1 = radians(lat1)
    p2 = radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return r * c


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{count} {label}" for label, count in items)


def _fetch_user_personalization_profile(cursor, user_id: str) -> Optional[dict[str, Any]]:
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
    row = cursor.fetchone()
    if not row:
        return None

    profile_user_id, profile_payload, updated_at = row
    return {
        "user_id": profile_user_id,
        "profile_payload": profile_payload,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def _fetch_features(cursor, job: AnalysisJob) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT started_at, ended_at, label
        FROM sessions
        WHERE id = %s
        """,
        (job.session_id,),
    )
    session_row = cursor.fetchone()
    if not session_row:
        raise RuntimeError(f"Session {job.session_id} not found")
    started_at, ended_at, label = session_row
    if ended_at is None:
        raise RuntimeError(f"Session {job.session_id} has no ended_at yet")

    user_profile = _fetch_user_personalization_profile(cursor, job.user_id)

    cursor.execute(
        """
        SELECT metadata
        FROM events
        WHERE session_id = %s
          AND label = 'pre_session_context'
          AND (
                metadata ? 'activity_context'
             OR metadata ? 'environment_context'
             OR metadata ? 'mental_readiness'
          )
        ORDER BY time DESC
        LIMIT 1
        """,
        (job.session_id,),
    )
    end_marker_row = cursor.fetchone()
    if not end_marker_row:
        cursor.execute(
            """
            SELECT metadata
            FROM events
            WHERE session_id = %s
              AND label = 'session_marker'
              AND val_text = 'END'
              AND (
                    metadata ? 'activity_context'
                 OR metadata ? 'environment_context'
                 OR metadata ? 'mental_readiness'
              )
            ORDER BY time DESC
            LIMIT 1
            """,
            (job.session_id,),
        )
        end_marker_row = cursor.fetchone()
    if not end_marker_row:
        cursor.execute(
            """
            SELECT metadata
            FROM events
            WHERE session_id = %s
              AND label = 'session_marker'
              AND val_text = 'END'
            ORDER BY time DESC
            LIMIT 1
            """,
            (job.session_id,),
        )
        end_marker_row = cursor.fetchone()
    end_marker_meta = end_marker_row[0] if end_marker_row else None
    activity_context = None
    environment_context = None
    mental_readiness = None
    if isinstance(end_marker_meta, str):
        try:
            end_marker_meta = json.loads(end_marker_meta)
        except Exception:
            end_marker_meta = None

    if isinstance(end_marker_meta, dict):
        activity_context = end_marker_meta.get("activity_context")
        environment_context = end_marker_meta.get("environment_context")
        mental_readiness = end_marker_meta.get("mental_readiness")

    cursor.execute(
        """
        SELECT time, label, confidence, ai_label
        FROM audio_events
        WHERE session_id = %s
          AND time >= %s
          AND time <= %s
        ORDER BY time ASC
        """,
        (job.session_id, started_at, ended_at),
    )
    audio_rows = cursor.fetchall()

    raw_audio_events: list[dict[str, Any]] = []

    label_counts: dict[str, int] = {}
    ai_counts: dict[str, int] = {}
    speech_count = 0
    silent_count = 0
    other_count = 0
    qualified_audio_count = 0
    for t, label_val, confidence, ai_label in audio_rows:
        conf = _normalize_confidence(confidence)
        if conf is None or conf <= 0.5:
            continue
        raw_audio_events.append(
            {
                "time": t.isoformat() if hasattr(t, "isoformat") else str(t),
                "label": label_val,
                "confidence": confidence,
                "ai_label": ai_label,
            }
        )
        qualified_audio_count += 1
        normalized_label = (label_val or "unknown").strip().lower()
        label_counts[normalized_label] = label_counts.get(normalized_label, 0) + 1

        if ai_label:
            ai_key = str(ai_label).strip()
            ai_counts[ai_key] = ai_counts.get(ai_key, 0) + 1

        ai_norm = str(ai_label).strip().lower() if ai_label else ""
        speech_labels = {"speech", "talking", "conversation", "voice", "narration", "speaking"}
        silent_labels = {"silence", "silent", "quiet", "still"}
        if normalized_label in speech_labels or ai_norm in speech_labels:
            speech_count += 1
        elif normalized_label in silent_labels or ai_norm in silent_labels:
            silent_count += 1
        else:
            other_count += 1

    cursor.execute(
        """
        SELECT time, metric_code, value
        FROM vitals
        WHERE session_id = %s
          AND time >= %s
          AND time <= %s
        ORDER BY time ASC
        """,
        (job.session_id, started_at, ended_at),
    )
    vital_rows = cursor.fetchall()

    raw_vitals: list[dict[str, Any]] = []
    raw_audio_exposure: list[dict[str, Any]] = []

    hr_values: list[float] = []
    steps_values: list[float] = []
    distance_values: list[float] = []
    audio_exposure_values: list[float] = []
    for t, metric_code, value in vital_rows:
        val = _to_float(value)
        if val is None:
            continue
        if metric_code == 1:
            raw_vitals.append(
                {
                    "time": t.isoformat() if hasattr(t, "isoformat") else str(t),
                    "metric_code": metric_code,
                    "value": value,
                }
            )
            hr_values.append(val)
        elif metric_code == 10:
            if val < 0:
                continue
            raw_audio_exposure.append(
                {
                    "time": t.isoformat() if hasattr(t, "isoformat") else str(t),
                    "value": value,
                }
            )
            audio_exposure_values.append(val)
        elif metric_code == 20:
            raw_vitals.append(
                {
                    "time": t.isoformat() if hasattr(t, "isoformat") else str(t),
                    "metric_code": metric_code,
                    "value": value,
                }
            )
            steps_values.append(val)
        elif metric_code == 21:
            raw_vitals.append(
                {
                    "time": t.isoformat() if hasattr(t, "isoformat") else str(t),
                    "metric_code": metric_code,
                    "value": value,
                }
            )
            distance_values.append(val)

    cursor.execute(
        """
        SELECT time, lat, lon, acc
        FROM gps
        WHERE session_id = %s
          AND time >= %s
          AND time <= %s
          AND lat IS NOT NULL
          AND lon IS NOT NULL
        ORDER BY time ASC
        """,
        (job.session_id, started_at, ended_at),
    )
    gps_rows = cursor.fetchall()

    raw_gps: list[dict[str, Any]] = []
    for t, lat, lon, acc in gps_rows:
        raw_gps.append(
            {
                "time": t.isoformat() if hasattr(t, "isoformat") else str(t),
                "lat": lat,
                "lon": lon,
                "acc": acc,
            }
        )

    displacement_m = None
    total_travel_m = None
    if len(gps_rows) >= 2:
        _, start_lat, start_lon, _ = gps_rows[0]
        _, end_lat, end_lon, _ = gps_rows[-1]
        displacement_m = _haversine_m(start_lat, start_lon, end_lat, end_lon)
        travel = 0.0
        for i in range(1, len(gps_rows)):
            _, lat1, lon1, _ = gps_rows[i - 1]
            _, lat2, lon2, _ = gps_rows[i]
            travel += _haversine_m(lat1, lon1, lat2, lon2)
        total_travel_m = travel

    drastic_location_change = bool(
        displacement_m is not None and total_travel_m is not None and (displacement_m >= 300 or total_travel_m >= 1000)
    )

    cursor.execute(
        """
        SELECT time, context
        FROM motion_events
        WHERE session_id = %s
          AND time >= %s
          AND time <= %s
        ORDER BY time ASC
        """,
        (job.session_id, started_at, ended_at),
    )
    motion_rows = cursor.fetchall()
    raw_motion_events: list[dict[str, Any]] = []
    motion_counts: dict[str, int] = {}
    stationary_labels = {"stationary", "still", "sitting", "idle"}
    active_labels = {"walking", "running", "cycling", "automotive", "moving"}
    stationary_count = 0
    active_count = 0
    for t, context in motion_rows:
        context_norm = str(context or "unknown").strip().lower()
        if context_norm == "unknown":
            context_norm = "non-stationary"
        raw_motion_events.append(
            {
                "time": t.isoformat() if hasattr(t, "isoformat") else str(t),
                "context": context_norm,
            }
        )
        motion_counts[context_norm] = motion_counts.get(context_norm, 0) + 1
        if context_norm in stationary_labels:
            stationary_count += 1
        elif context_norm in active_labels:
            active_count += 1

    non_stationary_count = len(motion_rows) - stationary_count
    if motion_rows and stationary_count > 0 and non_stationary_count == 0:
        motion_focus_state = "focused"
    else:
        motion_focus_state = "unconcentrated"

    duration_min = max((ended_at - started_at).total_seconds() / 60.0, 0.0)

    return {
        "session": {
            "id": job.session_id,
            "user_id": job.user_id,
            "device_id": job.device_id,
            "label": label,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_min": duration_min,
        },
        "pre_session_context": {
            "activity_context": activity_context,
            "environment_context": environment_context,
            "mental_readiness": mental_readiness,
        },
        "user_personalization_profile": user_profile,
        "counts": {
            "audio_events_total": len(audio_rows),
            "audio_events_conf_gt_50": qualified_audio_count,
            "vitals_rows": len(vital_rows),
            "gps_points": len(gps_rows),
            "motion_events": len(motion_rows),
        },
        "raw": {
            "audio_events": raw_audio_events,
            "vitals": raw_vitals,
            "audio_exposure": raw_audio_exposure,
            "gps": raw_gps,
            "motion_events": raw_motion_events,
        },
        "audio": {
            "speech_events_conf_gt_50": speech_count,
            "silent_events_conf_gt_50": silent_count,
            "other_events_conf_gt_50": other_count,
            "speech_ratio_conf_gt_50": (
                round(speech_count / qualified_audio_count, 4) if qualified_audio_count else None
            ),
            "avg_audio_exposure_db": (
                sum(audio_exposure_values) / len(audio_exposure_values)
                if audio_exposure_values
                else None
            ),
            "audio_exposure_samples": len(audio_exposure_values),
            "label_counts_conf_gt_50": label_counts,
            "label_counts_conf_gt_50_text": _format_counts(label_counts),
            "ai_label_counts_conf_gt_50": ai_counts,
            "ai_label_counts_conf_gt_50_text": _format_counts(ai_counts),
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
            "counts_text": _format_counts(motion_counts),
            "stationary_count": stationary_count,
            "active_count": active_count,
            "focus_state": motion_focus_state,
        },
    }


def _build_prompt(features: dict[str, Any]) -> str:
    session = features["session"]
    raw = features.get("raw") or {}
    user_profile = features.get("user_personalization_profile")
    user_profile_text = None
    if user_profile and user_profile.get("profile_payload") is not None:
        if isinstance(user_profile["profile_payload"], str):
            user_profile_text = user_profile["profile_payload"]
        else:
            user_profile_text = json.dumps(user_profile["profile_payload"], ensure_ascii=False)

    return f"""You are an expert study concentration analyst. Use Compositional Chain-of-Thought (CCoT) with explicit 3-phase reasoning.

SESSION WINDOW:
- session_id: {session['id']}
- started_at: {session['started_at']}
- ended_at: {session['ended_at']}
- duration_min: {session['duration_min']}

RAW SENSOR ROWS (entire session window, use these as the primary evidence):
{json.dumps(raw, ensure_ascii=False)}

NOTE: raw.vitals includes only heart rate (metric_code=1), steps (20), and distance (21). Audio exposure dBA is in raw.audio_exposure and should NOT be treated as heart rate.

USER PERSONALIZATION PROFILE:
{user_profile_text or "(none)"}

PRE-SESSION CONTEXT USAGE RULE:
- Compare the current pre-session context (activity/environment/mental readiness) with any prior contexts described in the user profile.
- If the profile shows the user focused well in a similar context (e.g., activity=reading, environment=cafe, mental_readiness=high), treat that as positive evidence for higher concentration in this session.
- If the profile shows poor focus in a similar context, treat it as negative evidence.
- If there is no matching context in the profile, do not assume a benefit or penalty from context alone.

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
   
NORMAL CONCENTRATION THRESHOLDS:
1) Audio environment (dBA from audio exposure):
    - Good focus: avg dBA <= 45
    - Moderate: 45 < avg dBA <= 60
    - Distracting: avg dBA > 60
    - Speech vs silent labels indicate whether the environment is conversational vs quiet study.
2) Vitals / movement:
   - Heart rate 60-85 bpm generally supports calm focus.
   - Lower steps/distance supports seated studying; higher values may indicate reduced concentration.
3) GPS stability:
   - Stable location supports concentration.
   - Large displacement or high travel suggests context switching/interruptions.
4) Motion events (hard rule):
    - If all motion contexts are stationary/still/sitting/idle, mark user as focused.
    - If any context is unknown or non-stationary, mark user as unconcentrated.

PHASE 1: INDIVIDUAL MODALITY DECOMPOSITION
Step 1 (Audio only, iPhone): use audio context labels + audio exposure (dBA) from metric_code=10. Ignore everything else.
Step 2 (Heart rate only, Apple Watch): use heart rate metric_code=1 only. Ignore steps, distance, GPS, motion, and audio here.
Step 3 (Movement only): use GPS (iPhone) + motion context (iPhone + Watch) + steps/distance (Apple Watch). Ignore audio and heart rate here.
Step 4 (Strict exclusion): do NOT use any other data outside steps 1-3 (including any other metrics, metadata, or profiles).

PHASE 2: CROSS-MODAL COMPOSITION
Step 4. Identify correlations across modalities (e.g., noisy + active motion + movement in location).
Step 5. Synthesize a holistic concentration assessment from all modalities.

PHASE 3: ACTIONABLE SYNTHESIS
Step 6. Provide concise personalized recommendations tied to root causes from the composed analysis.

SCORING RULE:
- Score primarily based on deviation from the user's personal focused-state baseline.
- Do NOT penalize differences from population averages if they align with the user's normal focused behavior.
- Penalize strong multi-modal signals of distraction (e.g., noisy + active motion + large movement).
- Use generic thresholds as secondary anchors when personalization is weak or missing for a modality.

OUTPUT REQUIREMENTS:
Return ONLY valid JSON with this exact structure:
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
  "reason": "<2 to 3 sentences explaining why this score was assigned>"
}}

Final rules:
- score must be 1-10 integer.
- reason must be 2-3 sentences.
- Ensure the reason references audio, vitals, GPS, and motion evidence.
- No text outside JSON.
"""


def _call_llm_openai(prompt: str) -> tuple[int, str, str]:
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("LLM_API_KEY is not configured")

    model = settings.llm_model
    body = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    }
                ],
            }
        ],
        "text": {"format": {"type": "json_object"}},
    }

    payload = json.dumps(body).encode("utf-8")
    req = request.Request(
        url=settings.llm_api_base_url.rstrip("/") + "/responses",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with request.urlopen(req, timeout=settings.llm_timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"LLM request failed ({exc.code}): {detail}") from exc

    data = json.loads(raw)
    text_output = data.get("output_text")
    if not text_output:
        # Fallback parsing for cases where output_text is absent.
        output_items = data.get("output") or []
        chunks = []
        for item in output_items:
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    chunks.append(content["text"])
        text_output = "\n".join(chunks)

    if not text_output:
        raise RuntimeError("LLM response did not contain output text")

    parsed = _parse_model_json(text_output)
    score = parsed.get("score")
    reason = parsed.get("reason")

    if score is None or reason is None:
        raise RuntimeError(f"LLM output missing required fields: {text_output}")

    try:
        score_int = int(score)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid score from LLM: {score}") from exc

    if score_int < 1 or score_int > 10:
        raise RuntimeError(f"Score out of range 1-10: {score_int}")

    reason_str = str(reason).strip()
    if not reason_str:
        raise RuntimeError("Reason from LLM was empty")

    return score_int, reason_str, text_output


def _call_llm_snowflake(prompt: str) -> tuple[int, str, str]:
    try:
        from snowflake.snowpark import Session
    except Exception as exc:
        raise RuntimeError("snowflake-snowpark-python is required for LLM_PROVIDER=snowflake") from exc

    required = {
        "account": settings.snowflake_account,
        "user": settings.snowflake_user,
        "password": settings.snowflake_user_password,
        "role": settings.snowflake_role,
        "database": settings.snowflake_database,
        "schema": settings.snowflake_schema,
        "warehouse": settings.snowflake_warehouse,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing Snowflake settings for Cortex call: {', '.join(missing)}")

    escaped_prompt = prompt.replace("'", "''")
    model = settings.llm_model
    temperature = settings.llm_temperature
    top_p = settings.llm_top_p
    query = (
        "SELECT SNOWFLAKE.CORTEX.COMPLETE("
        f"'{model}', "
        f"'{escaped_prompt}'"
        ") AS response"
    )

    session = Session.builder.configs(required).create()
    try:
        rows = session.sql(query).collect()
    finally:
        session.close()

    if not rows:
        raise RuntimeError("Snowflake Cortex returned no rows")

    response_text = rows[0]["RESPONSE"]
    parsed = _parse_model_json(response_text)
    score = parsed.get("score")
    reason = parsed.get("reason")

    if score is None or reason is None:
        raise RuntimeError(f"Cortex output missing required fields: {response_text}")

    try:
        score_int = int(score)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid score from Cortex: {score}") from exc

    if score_int < 1 or score_int > 10:
        raise RuntimeError(f"Score out of range 1-10: {score_int}")

    reason_str = str(reason).strip()
    if not reason_str:
        raise RuntimeError("Reason from Cortex was empty")

    return score_int, reason_str, response_text


def _call_llm(prompt: str) -> tuple[int, str, str]:
    provider = (settings.llm_provider or "openai").strip().lower()
    if provider == "snowflake":
        return _call_llm_snowflake(prompt)
    if provider == "openai":
        return _call_llm_openai(prompt)
    raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider}")


def _parse_model_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not parse JSON from LLM output: {text}")

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse JSON from extracted content: {text}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"Parsed LLM JSON was not an object: {text}")
    return parsed


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _emit_ccot_output(session_id: int, payload: dict[str, Any]) -> None:
    """Write the latest concentration result to llm/CCoT/output.

    This intentionally overwrites the same single file each time so that
    consumers only need to look in one place.
    """
    _atomic_write_json(_ccot_output_path, payload)

    # Best-effort cleanup of legacy artifacts so the output directory contains
    # only a single "latest" JSON file.
    for legacy_path in _legacy_output_paths:
        try:
            if legacy_path.exists():
                legacy_path.unlink()
        except Exception:
            logger.exception("ccot_output_cleanup_failed path=%s", legacy_path)

    try:
        for legacy_per_session_path in _ccot_output_dir.glob("concentration_result_session_*.json"):
            try:
                legacy_per_session_path.unlink()
            except Exception:
                logger.exception("ccot_output_cleanup_failed path=%s", legacy_per_session_path)
    except Exception:
        logger.exception("ccot_output_cleanup_glob_failed")


def _emit_pre_session_context(payload: dict[str, Any]) -> None:
    _atomic_write_json(_pre_session_context_path, payload)


def process_next_pending_job() -> bool:
    connection = None
    cursor = None
    job = None
    features: Optional[dict[str, Any]] = None

    try:
        connection = get_connection()
        cursor = connection.cursor()
        ensure_concentration_schema(cursor)
        job = _claim_next_job(cursor)
        connection.commit()

        if not job:
            return False

        logger.info("concentration_job_claimed session_id=%s", job.session_id)
        features = _fetch_features(cursor, job)
        prompt = _build_prompt(features)
        score, reason, llm_raw = _call_llm(prompt)
        llm_parsed = _parse_model_json(llm_raw)

        # Keep DB storage small: store the aggregated snapshot, not the full raw time series.
        features_for_storage = dict(features)
        features_for_storage.pop("raw", None)

        cursor.execute(
            """
            UPDATE session_concentration_analysis
            SET status = 'done',
                score = %s,
                reason = %s,
                model = %s,
                sensor_features = %s::jsonb,
                llm_raw_response = %s,
                error_message = NULL,
                completed_at = now(),
                updated_at = now()
            WHERE session_id = %s
            """,
            (
                score,
                reason,
                settings.llm_model,
                json.dumps(features_for_storage),
                llm_raw,
                job.session_id,
            ),
        )
        connection.commit()
        logger.info("concentration_job_done session_id=%s score=%s", job.session_id, score)

        try:
            payload: dict[str, Any] = {
                "phase_1": llm_parsed.get("phase_1") or {},
                "phase_2": llm_parsed.get("phase_2") or {},
                "phase_3": llm_parsed.get("phase_3") or {},
                "score": int(score),
                "reason": str(reason),
            }
            _emit_ccot_output(job.session_id, payload)

            pre_session_context = features.get("pre_session_context") or {}
            _emit_pre_session_context(
                {
                    "activity_context": pre_session_context.get("activity_context"),
                    "environment_context": pre_session_context.get("environment_context"),
                    "mental_readiness": pre_session_context.get("mental_readiness"),
                }
            )
        except Exception:
            logger.exception("concentration_output_write_failed session_id=%s", job.session_id)

        return True

    except Exception as exc:
        if connection:
            connection.rollback()
        logger.exception("concentration_job_failed session_id=%s", getattr(job, "session_id", None))

        # Attempt to mark failed using a new connection to avoid leaked tx state.
        if job is not None:
            fail_connection = None
            fail_cursor = None
            try:
                fail_connection = get_connection()
                fail_cursor = fail_connection.cursor()
                ensure_concentration_schema(fail_cursor)
                fail_cursor.execute(
                    """
                    UPDATE session_concentration_analysis
                    SET status = 'failed',
                        error_message = %s,
                        completed_at = now(),
                        updated_at = now()
                    WHERE session_id = %s
                    """,
                    (str(exc), job.session_id),
                )
                fail_connection.commit()
            except Exception:
                if fail_connection:
                    fail_connection.rollback()
                logger.exception("concentration_job_fail_mark_failed session_id=%s", job.session_id)
            finally:
                if fail_cursor:
                    fail_cursor.close()
                if fail_connection:
                    release_connection(fail_connection)

            try:
                payload: dict[str, Any] = {
                    "error_message": str(exc),
                }
                _emit_ccot_output(job.session_id, payload)
            except Exception:
                logger.exception("concentration_output_write_failed session_id=%s", job.session_id)
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)


class ConcentrationWorker:
    def __init__(self, poll_interval_seconds: float = 2.0):
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="concentration-worker", daemon=True)
        self._thread.start()
        logger.info("concentration_worker_started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("concentration_worker_stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            did_work = process_next_pending_job()
            if did_work:
                continue
            time.sleep(self._poll_interval_seconds)


def bootstrap_concentration_schema() -> None:
    connection = None
    cursor = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        ensure_concentration_schema(cursor)
        connection.commit()
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_connection(connection)
