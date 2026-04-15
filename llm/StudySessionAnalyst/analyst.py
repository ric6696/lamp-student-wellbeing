"""
Study Session Discrepancy Reasoning Agent

Reads concentration analysis output + user self-report,
then uses Snowflake Cortex LLM prompting only to generate
thoughtful discrepancy reasoning and actionable follow-ups.
"""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from snowflake.snowpark import Session

try:
    import psycopg2
except ImportError:
    psycopg2 = None


DISTRACTION_OPTIONS = [
    "Environmental Noise / Speech",
    "Movement / Restlessness",
    "Location Change / Transition",
    "Physiological Strain (stress, fatigue, discomfort)",
    "Internal Cognitive Drift (mind wandering, low motivation)",
    "Task Challenge (difficulty, frustration)",
    "No Major Distraction",
    "Others",
]


FACTOR_SENSOR_MAPPING = {
    "Environmental Noise / Speech": {
        "observability": "DIRECT",
        "primary_sensors": ["audio"],
        "secondary_sensors": ["vitals"],
        "confidence_cap": "HIGH",
        "notes": "Audio dB/events/speech signatures directly support this factor.",
    },
    "Movement / Restlessness": {
        "observability": "DIRECT",
        "primary_sensors": ["motion", "vitals"],
        "secondary_sensors": ["audio"],
        "confidence_cap": "HIGH",
        "notes": "Motion/step bursts are directly observable.",
    },
    "Location Change / Transition": {
        "observability": "DIRECT",
        "primary_sensors": ["gps", "motion"],
        "secondary_sensors": ["audio"],
        "confidence_cap": "HIGH",
        "notes": "GPS transitions and movement shifts directly indicate context changes.",
    },
    "Physiological Strain (stress, fatigue, discomfort)": {
        "observability": "INDIRECT",
        "primary_sensors": ["vitals"],
        "secondary_sensors": ["motion"],
        "confidence_cap": "MEDIUM",
        "notes": "Only inferred from physiology proxies.",
    },
    "Internal Cognitive Drift (mind wandering, low motivation)": {
        "observability": "UNMEASURED",
        "primary_sensors": [],
        "secondary_sensors": ["motion", "vitals"],
        "confidence_cap": "LOW",
        "notes": "Latent cognitive state; do not over-claim.",
    },
    "Task Challenge (difficulty, frustration)": {
        "observability": "UNMEASURED",
        "primary_sensors": [],
        "secondary_sensors": ["vitals", "motion"],
        "confidence_cap": "LOW",
        "notes": "No direct task metadata or objective difficulty labels.",
    },
    "No Major Distraction": {
        "observability": "DIRECT",
        "primary_sensors": ["audio", "motion", "vitals"],
        "secondary_sensors": ["gps"],
        "confidence_cap": "HIGH",
        "notes": "Supported when sensors are stable and non-disruptive.",
    },
    "Others": {
        "observability": "UNMEASURED",
        "primary_sensors": [],
        "secondary_sensors": [],
        "confidence_cap": "LOW",
        "notes": "Use when factor is not mappable.",
    },
}


def safe_get(payload, key, default=None):
    value = payload.get(key, default)
    return default if value is None else value


def normalize_selected_factors(raw_factor):
    if raw_factor is None:
        return ["Others"]

    candidates = []
    if isinstance(raw_factor, list):
        candidates = [str(item).strip() for item in raw_factor if str(item).strip()]
    elif isinstance(raw_factor, str):
        text = raw_factor.strip()
        # Allow simple multi-select serialization like "A + B" or "A, B".
        if "+" in text:
            candidates = [part.strip() for part in text.split("+") if part.strip()]
        elif "," in text:
            candidates = [part.strip() for part in text.split(",") if part.strip()]
        elif text:
            candidates = [text]
    else:
        text = str(raw_factor).strip()
        if text:
            candidates = [text]

    normalized = [item for item in candidates if item in DISTRACTION_OPTIONS]
    if not normalized:
        return ["Others"]

    seen = set()
    deduped = []
    for item in normalized:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def extract_concentration_context(concentration_payload):
    """Extract concentration context from the current phase-based CCoT schema."""

    sensor_features = concentration_payload.get("sensor_features") or {}
    counts = sensor_features.get("counts") or {}
    audio = sensor_features.get("audio") or {}
    vitals = sensor_features.get("vitals") or {}
    gps = sensor_features.get("gps") or {}
    motion = sensor_features.get("motion") or {}

    score = safe_get(concentration_payload, "score", None)
    if score is None:
        raise ValueError("Missing score in concentration analysis JSON")

    reason = safe_get(concentration_payload, "reason", "")
    if not reason:
        phase_2 = concentration_payload.get("phase_2", {}) or {}
        reason = safe_get(phase_2, "holistic_assessment", "")

    phase_1 = concentration_payload.get("phase_1", {}) or {}
    phase_2 = concentration_payload.get("phase_2", {}) or {}
    phase_3 = concentration_payload.get("phase_3", {}) or {}

    return {
        "timestamp": safe_get(concentration_payload, "timestamp", ""),
        "model": safe_get(concentration_payload, "model", ""),
        "session": safe_get(concentration_payload, "session", {}),
        "sensor_features": sensor_features,
        "phase_1": phase_1,
        "phase_2": phase_2,
        "phase_3": phase_3,
        "personalization_basis": safe_get(concentration_payload, "personalization_basis", None),
        "score": float(score),
        "reason": reason,
        "sensor_statistics": {
            "avg_heart_rate": safe_get(vitals, "avg_heart_rate", None),
            "avg_noise_level": safe_get(audio, "avg_db_conf_gt_50", None),
            "avg_steps": safe_get(vitals, "avg_steps", None),
            "num_readings": safe_get(counts, "vitals_rows", None),
            "gps_points": safe_get(gps, "points", None),
            "motion_events": safe_get(motion, "counts", None),
        },
    }


def extract_user_reflection(user_payload):
    """Parse simplified question-answer JSON format only."""

    simple_rows = user_payload.get("user_response")
    if not isinstance(simple_rows, list):
        raise ValueError("Expected 'user_response' as a list of question-answer objects")

    focus_score = None
    main_factor = None
    qa_pairs = []

    for row in simple_rows:
        if not isinstance(row, dict):
            continue
        question = str(safe_get(row, "question", "")).strip()
        answer = safe_get(row, "answer", None)
        qa_pairs.append({"question": question, "answer": answer})

        q_lower = question.lower()
        if "how focused" in q_lower or "focus" in q_lower:
            focus_score = answer
        if "what most affected" in q_lower or "affected your concentration" in q_lower:
            main_factor = answer

    normalized_factors = normalize_selected_factors(main_factor)
    primary_factor = normalized_factors[0]

    return {
        "self_focus_score": focus_score,
        "selected_factors": normalized_factors,
        "primary_factor": primary_factor,
        "primary_factor_mapping": FACTOR_SENSOR_MAPPING.get(primary_factor, FACTOR_SENSOR_MAPPING["Others"]),
        "question_answer_pairs": qa_pairs,
    }


def validate_inputs(concentration_ctx, user_response):
    if concentration_ctx["score"] is None:
        raise ValueError("Missing score in concentration analysis JSON")
    if user_response["self_focus_score"] is None:
        raise ValueError("Missing self_focus_score in user response JSON")

    concentration_ctx["score"] = float(concentration_ctx["score"])
    user_response["self_focus_score"] = float(user_response["self_focus_score"])


def build_thoughtful_prompt(concentration_ctx, user_response):
    """Create a prompt for discrepancy analysis with sensor interpretation hints."""
    return f"""You are an educational analytics reasoning assistant.
Your task is to compare the model-predicted concentration score and the user's self-reported score,
then produce a discrepancy analysis report in a practical format for prediction-agent calibration.

Primary objective:
1) Quantify and explain the score difference.
2) Provide guidance for how the prediction agent should judge each sensor next time.
3) Clarify uncertainty and weighting priorities for the next prediction cycle.

Rules:
- Be evidence-grounded: use only provided input fields.
- Be balanced: do not assume model is always correct or user is always correct.
- Keep conclusions concise and operational for model/pipeline tuning.
- Explicitly mention uncertainty and possible blind spots.
- Do not include chain-of-thought; output conclusions only.

Input A - Model Concentration Output:
{json.dumps(concentration_ctx, ensure_ascii=False, indent=2)}

Input B - User Response:
{json.dumps(user_response, ensure_ascii=False, indent=2)}

Input C - Canonical Distraction Options:
{json.dumps(DISTRACTION_OPTIONS, ensure_ascii=False, indent=2)}

Input D - Category to Sensor Mapping Policy:
{json.dumps(FACTOR_SENSOR_MAPPING, ensure_ascii=False, indent=2)}

Return STRICT JSON only (no markdown, no extra text) using this exact schema:
{{
    "overview": {{
        "model_score": number,
        "user_score": number,
        "gap": number,
        "agreement": "MODEL_HIGHER|USER_HIGHER|ALIGNED",
        "summary": "..."
    }},
    "selected_factors": ["...", "..."],
    "factor_sensor_mappings": [
        {{
            "factor": "...",
            "mapped_observability": "DIRECT|INDIRECT|LIMITED|UNMEASURED",
            "mapped_primary_sensors": ["..."],
            "confidence_cap": "LOW|MEDIUM|HIGH",
            "alignment_with_session_sensors": "ALIGNED|PARTIAL|UNSUPPORTED|UNMEASURED"
        }}
    ],
    "why_difference": "...",
    "prediction_agent_sensor_judgment": {{
        "audio": "...",
        "vitals": "...",
        "motion": "...",
        "gps": "..."
    }},
    "judgment_policy_next_run": ["...", "..."]
}}

Requirements:
- Keep it concise; avoid long paragraphs.
- `gap` must be absolute numeric difference between model and user score.
- `selected_factors` must contain one or more values from Input C.
- Use Input D exactly for mapped_observability, mapped sensors, and confidence_cap.
- If the factor observability is UNMEASURED or LIMITED, avoid strong causal claims.
- `agreement` rules:
    - MODEL_HIGHER: model_score > user_score
    - USER_HIGHER: user_score > model_score
    - ALIGNED: same score
- `prediction_agent_sensor_judgment` should describe sensor reliability, weighting, and caution points by sensor group.
- Do not include user behavior recommendations.
"""


def run_cortex_complete(session, model, prompt):
    escaped_prompt = prompt.replace("'", "''")

    query = f"""
    SELECT SNOWFLAKE.CORTEX.COMPLETE(
        '{model}',
        '{escaped_prompt}'
    ) AS response
    """

    result = session.sql(query).collect()
    if not result:
        raise RuntimeError("No response from SNOWFLAKE.CORTEX.COMPLETE")
    return result[0]["RESPONSE"]


def extract_json_object(text):
    stripped = text.strip()

    try:
        return json.loads(stripped)
    except Exception:
        pass

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and first < last:
        snippet = stripped[first : last + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None

    return None


def _score_direction(model_score, user_score):
    if model_score > user_score:
        return "MODEL_HIGHER"
    if user_score > model_score:
        return "USER_HIGHER"
    return "ALIGNED"


def sanitize_structured_result(result, concentration_ctx, user_response):
    model_score = float(concentration_ctx["score"])
    user_score = float(user_response["self_focus_score"])
    gap = abs(model_score - user_score)

    overview_in = result.get("overview", {}) if isinstance(result, dict) else {}
    summary = overview_in.get("summary") or ""
    if not isinstance(summary, str) or not summary.strip():
        summary = "Structured discrepancy result generated."

    selected_raw = None
    if isinstance(result, dict):
        selected_raw = result.get("selected_factors")
        if selected_raw is None and result.get("selected_factor") is not None:
            selected_raw = [result.get("selected_factor")]
    if selected_raw is None:
        selected_raw = user_response.get("selected_factors", ["Others"])
    selected_factors = normalize_selected_factors(selected_raw)

    alignment_lookup = {}
    if isinstance(result, dict) and isinstance(result.get("factor_sensor_mappings"), list):
        for item in result.get("factor_sensor_mappings", []):
            if isinstance(item, dict):
                key = str(item.get("factor", "")).strip()
                if key:
                    alignment_lookup[key] = item.get("alignment_with_session_sensors", "UNMEASURED")

    factor_sensor_mappings = []
    for factor in selected_factors:
        mapping = FACTOR_SENSOR_MAPPING.get(factor, FACTOR_SENSOR_MAPPING["Others"])
        alignment = alignment_lookup.get(factor, "UNMEASURED")
        if alignment not in {"ALIGNED", "PARTIAL", "UNSUPPORTED", "UNMEASURED"}:
            alignment = "UNMEASURED"
        factor_sensor_mappings.append(
            {
                "factor": factor,
                "mapped_observability": mapping.get("observability", "UNMEASURED"),
                "mapped_primary_sensors": mapping.get("primary_sensors", []),
                "confidence_cap": mapping.get("confidence_cap", "LOW"),
                "alignment_with_session_sensors": alignment,
            }
        )

    why_difference_raw = result.get("why_difference", "") if isinstance(result, dict) else ""
    if isinstance(why_difference_raw, list):
        why_difference = " ".join(str(item).strip() for item in why_difference_raw if str(item).strip())
    else:
        why_difference = str(why_difference_raw).strip()
    if not why_difference:
        why_difference = "No concise discrepancy rationale was returned."

    sensor_in = result.get("prediction_agent_sensor_judgment", {}) if isinstance(result, dict) else {}
    policy_in = result.get("judgment_policy_next_run", []) if isinstance(result, dict) else []
    if not isinstance(policy_in, list):
        policy_in = [str(policy_in)] if str(policy_in).strip() else []

    return {
        "overview": {
            "model_score": model_score,
            "user_score": user_score,
            "gap": round(gap, 3),
            "agreement": _score_direction(model_score, user_score),
            "summary": summary,
        },
        "selected_factors": selected_factors,
        "factor_sensor_mappings": factor_sensor_mappings,
        "why_difference": why_difference,
        "prediction_agent_sensor_judgment": {
            "audio": str(sensor_in.get("audio", "")),
            "vitals": str(sensor_in.get("vitals", "")),
            "motion": str(sensor_in.get("motion", "")),
            "gps": str(sensor_in.get("gps", "")),
        },
        "judgment_policy_next_run": [str(item) for item in policy_in[:2]],
    }


def analyze_with_llm_only(session, model, concentration_ctx, user_response):
    prompt = build_thoughtful_prompt(concentration_ctx, user_response)
    raw_response = run_cortex_complete(session=session, model=model, prompt=prompt)

    parsed = extract_json_object(raw_response)
    if parsed is None:
        model_score = concentration_ctx["score"]
        user_score = user_response["self_focus_score"]
        score_gap = abs(model_score - user_score)
        direction = _score_direction(model_score, user_score)

        parsed = {
            "overview": {
                "model_score": model_score,
                "user_score": user_score,
                "gap": round(score_gap, 3),
                "agreement": direction,
                "summary": "LLM response was not valid JSON.",
            },
            "selected_factors": user_response.get("selected_factors", ["Others"]),
            "factor_sensor_mappings": [
                {
                    "factor": factor,
                    "mapped_observability": FACTOR_SENSOR_MAPPING.get(factor, FACTOR_SENSOR_MAPPING["Others"]).get(
                        "observability", "UNMEASURED"
                    ),
                    "mapped_primary_sensors": FACTOR_SENSOR_MAPPING.get(
                        factor,
                        FACTOR_SENSOR_MAPPING["Others"],
                    ).get("primary_sensors", []),
                    "confidence_cap": FACTOR_SENSOR_MAPPING.get(factor, FACTOR_SENSOR_MAPPING["Others"]).get(
                        "confidence_cap", "LOW"
                    ),
                    "alignment_with_session_sensors": "UNMEASURED",
                }
                for factor in user_response.get("selected_factors", ["Others"])
            ],
            "why_difference": "LLM output parsing failed.",
            "prediction_agent_sensor_judgment": {
                "audio": "",
                "vitals": "",
                "motion": "",
                "gps": "",
            },
            "judgment_policy_next_run": [],
        }

    clean_result = sanitize_structured_result(parsed, concentration_ctx, user_response)
    return clean_result


def append_output_history(output_file: Path, output_payload: dict):
    """Write only the latest concise run result."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)


def process_discrepancy_reasoning(
    concentration_path,
    user_response_path,
    output_path,
    pre_session_questions_path="CCoT/output/pre_session_context.json",
    model="claude-sonnet-4-5",
    store_to_db=True,
    db_user_id=None,
    db_device_id=None,
    db_session_id=None,
):
    load_dotenv()

    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    concentration_file = _resolve_path(concentration_path, project_root)
    user_response_file = _resolve_path(user_response_path, project_root)
    output_file = _resolve_output_path(output_path, project_root)
    pre_session_questions = None

    if pre_session_questions_path:
        pre_session_file = _resolve_path(pre_session_questions_path, project_root)
        with open(pre_session_file, "r", encoding="utf-8") as f:
            pre_session_questions = json.load(f)

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
    print("Study Session Discrepancy Reasoning Agent (Single Output)")
    print("=" * 100)

    with open(concentration_file, "r", encoding="utf-8") as f:
        concentration_payload = json.load(f)

    with open(user_response_file, "r", encoding="utf-8") as f:
        user_payload = json.load(f)

    concentration_ctx = extract_concentration_context(concentration_payload)
    user_response = extract_user_reflection(user_payload)
    validate_inputs(concentration_ctx, user_response)

    print("Connecting to Snowflake...")
    session = Session.builder.configs(connection_params).create()
    print("✓ Connected")

    try:
        structured_result = analyze_with_llm_only(
            session=session,
            model=model,
            concentration_ctx=concentration_ctx,
            user_response=user_response,
        )

        output_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "pre_session_questions": pre_session_questions,
            "result": structured_result,
        }

        append_output_history(output_file=output_file, output_payload=output_payload)
        print(f"✓ Output appended to history JSON: {output_file}")

        if store_to_db:
            discrepancy_id = save_discrepancy_to_postgres(
                output_payload=output_payload,
                user_id=db_user_id,
                device_id=db_device_id,
                session_id=db_session_id,
            )
            print(f"✓ Stored discrepancy reasoning in Postgres (id={discrepancy_id})")

        return output_payload

    finally:
        session.close()
        print("✓ Snowflake session closed")


def save_discrepancy_to_postgres(output_payload, user_id, device_id=None, session_id=None):
    if psycopg2 is None:
        raise RuntimeError(
            "psycopg2 is not installed. Install it with `pip install psycopg2-binary` in the active environment."
        )

    if not user_id:
        raise ValueError("db_user_id is required when DB persistence is enabled")

    discrepancy_reasoning = output_payload["result"]
    discrepancy_overview = discrepancy_reasoning.get("overview", {})
    pre_session_questions = output_payload.get("pre_session_questions")

    connection = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        dbname=os.getenv("POSTGRES_DB", "sensing_db"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "dev_password"),
    )

    try:
        with connection:
            with connection.cursor() as cursor:
                has_pre_session_questions = _column_exists(
                    cursor,
                    table_name="session_discrepancy_analyses",
                    column_name="pre_session_questions",
                )

                cursor.execute(
                    "INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                    (user_id.lower(),),
                )

                if device_id:
                    cursor.execute(
                        "INSERT INTO devices (id, user_id, last_sync) VALUES (%s, %s, NOW()) "
                        "ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id, last_sync = NOW()",
                        (device_id.lower(), user_id.lower()),
                    )

                if has_pre_session_questions:
                    cursor.execute(
                        """
                        INSERT INTO session_discrepancy_analyses (
                            user_id,
                            device_id,
                            session_id,
                            model_name,
                            model_score,
                            user_score,
                            score_gap,
                            pre_session_questions,
                            discrepancy_reasoning,
                            raw_llm_response,
                            prompt_used
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                        RETURNING id
                        """,
                        (
                            user_id.lower(),
                            device_id.lower() if device_id else None,
                            session_id,
                            output_payload.get("model"),
                            discrepancy_overview.get("model_score"),
                            discrepancy_overview.get("user_score"),
                            discrepancy_overview.get("gap"),
                            json.dumps(pre_session_questions, ensure_ascii=False)
                            if pre_session_questions is not None
                            else None,
                            json.dumps(discrepancy_reasoning, ensure_ascii=False),
                            None,
                            None,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO session_discrepancy_analyses (
                            user_id,
                            device_id,
                            session_id,
                            model_name,
                            model_score,
                            user_score,
                            score_gap,
                            discrepancy_reasoning,
                            raw_llm_response,
                            prompt_used
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                        RETURNING id
                        """,
                        (
                            user_id.lower(),
                            device_id.lower() if device_id else None,
                            session_id,
                            output_payload.get("model"),
                            discrepancy_overview.get("model_score"),
                            discrepancy_overview.get("user_score"),
                            discrepancy_overview.get("gap"),
                            json.dumps(discrepancy_reasoning, ensure_ascii=False),
                            None,
                            None,
                        ),
                    )
                row = cursor.fetchone()
                discrepancy_id = row[0]
                return discrepancy_id
    finally:
        connection.close()


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def _resolve_path(raw_path: str, project_root: Path) -> Path:
    path_obj = Path(raw_path)
    if path_obj.is_absolute():
        if not path_obj.exists():
            raise FileNotFoundError(f"File not found: {path_obj}")
        return path_obj

    cwd_candidate = Path.cwd() / path_obj
    if cwd_candidate.exists():
        return cwd_candidate

    project_candidate = project_root / path_obj
    if project_candidate.exists():
        return project_candidate

    raise FileNotFoundError(
        f"File not found: {raw_path}. Tried {cwd_candidate} and {project_candidate}."
    )


def _resolve_output_path(raw_path: str, project_root: Path) -> Path:
    path_obj = Path(raw_path)
    if path_obj.is_absolute():
        return path_obj

    cwd_candidate = Path.cwd() / path_obj
    project_candidate = project_root / path_obj

    if cwd_candidate.parent.exists():
        return cwd_candidate

    return project_candidate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate discrepancy reasoning from concentration result + user response"
    )
    parser.add_argument(
        "--concentration",
        type=str,
        default="CCoT/output/concentration_analysis_results.json",
        help="Path to concentration analysis JSON",
    )
    parser.add_argument(
        "--user-response",
        type=str,
        default="StudySessionAnalyst/user_response_to_concentration.json",
        help="Path to user response JSON",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="StudySessionAnalyst/discrepancy_analysis_results.json",
        help="Path to save discrepancy reasoning JSON",
    )
    parser.add_argument(
        "--pre-session-questions",
        type=str,
        default="CCoT/output/pre_session_context.json",
        help="Path to pre-session questions JSON",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-5",
        help="Snowflake Cortex model name",
    )
    parser.add_argument(
        "--no-store-db",
        action="store_true",
        help="Disable PostgreSQL persistence for this run",
    )
    parser.add_argument(
        "--db-user-id",
        type=str,
        default=os.getenv("ANALYST_DEFAULT_USER_ID", "minsuk"),
        help="User ID for Postgres persistence (defaults to ANALYST_DEFAULT_USER_ID or minsuk)",
    )
    parser.add_argument(
        "--db-device-id",
        type=str,
        default=None,
        help="Device ID for Postgres persistence (optional)",
    )
    parser.add_argument(
        "--db-session-id",
        type=int,
        default=None,
        help="Session ID for Postgres persistence (optional)",
    )

    args = parser.parse_args()

    process_discrepancy_reasoning(
        concentration_path=args.concentration,
        user_response_path=args.user_response,
        output_path=args.output,
        pre_session_questions_path=args.pre_session_questions,
        model=args.model,
        store_to_db=not args.no_store_db,
        db_user_id=args.db_user_id,
        db_device_id=args.db_device_id,
        db_session_id=args.db_session_id,
    )
