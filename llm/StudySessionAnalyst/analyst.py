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


def safe_get(payload, key, default=None):
    value = payload.get(key, default)
    return default if value is None else value


def extract_concentration_context(concentration_payload):
    """Extract concentration context from the new CCoT output schema only."""
    sensor_features = concentration_payload.get("sensor_features", {})
    vitals = sensor_features.get("vitals", {})
    audio = sensor_features.get("audio", {})
    counts = sensor_features.get("counts", {})
    phase_2 = concentration_payload.get("phase_2", {})

    score = safe_get(concentration_payload, "concentration_score", None)
    if score is None:
        raise ValueError("Missing concentration_score in concentration analysis JSON")

    reasoning = safe_get(concentration_payload, "reason", "")
    if not reasoning:
        reasoning = safe_get(phase_2, "holistic_assessment", "")

    return {
        "timestamp": safe_get(concentration_payload, "timestamp", ""),
        "model": safe_get(concentration_payload, "model", ""),
        "concentration_score": score,
        "concentration_level": safe_get(concentration_payload, "concentration_level", None),
        "reasoning": reasoning,
        "sensor_statistics": {
            "avg_heart_rate": safe_get(vitals, "avg_heart_rate", None),
            "avg_noise_level": safe_get(audio, "avg_db_conf_gt_50", None),
            "avg_steps": safe_get(vitals, "avg_steps", None),
            "num_readings": safe_get(counts, "vitals_rows", None),
        },
    }


def extract_user_reflection(user_payload):
    """
    Handle the new realistic post-session user reflection schema only.
    """
    if "user_reflection" not in user_payload:
        raise ValueError("Expected new user response schema with 'user_reflection' key")

    refl = user_payload["user_reflection"]
    session_ref = user_payload.get("session_reference", {})

    major_disruptions = safe_get(refl, "major_disruptions", [])
    if not major_disruptions and safe_get(refl, "main_disruption", None):
        major_disruptions = [safe_get(refl, "main_disruption", None)]

    perceived_challenges = safe_get(refl, "perceived_challenges", {})
    if not perceived_challenges:
        perceived_challenges = {
            "environment": safe_get(refl, "environment_quality", None),
            "task_difficulty": safe_get(refl, "task_difficulty", None),
            "noise_level_estimated_db": safe_get(refl, "noise_level_estimated_db", None),
        }

    return {
        "self_focus_score": safe_get(refl, "overall_focus_rating", None),
        "overall_experience": safe_get(refl, "why_different", ""),
        "self_report_confidence": safe_get(refl, "overall_focus_confidence", None),
        "compared_to_model": safe_get(refl, "compared_to_model_result", None),
        "session_trajectory": safe_get(refl, "session_trajectory", None),
        "major_disruptions": major_disruptions,
        "perceived_challenges": perceived_challenges,
        "what_hurt_focus": safe_get(refl, "what_hurt_focus", []),
        "what_helped_focus": safe_get(refl, "what_helped_focus", []),
        "mental_fatigue_experienced": safe_get(refl, "mental_fatigue_experienced", None),
        "stress_level_peak": safe_get(refl, "stress_level_peak", None),
        "mind_wandering": safe_get(refl, "mind_wandering", None),
        "time_of_day": safe_get(refl, "time_of_day", safe_get(session_ref, "time_of_day", None)),
        "session_length_minutes": safe_get(
            refl,
            "session_length_minutes",
            safe_get(session_ref, "session_duration_minutes", None),
        ),
        "was_interrupted": safe_get(refl, "was_interrupted", False),
    }
def validate_inputs(concentration_ctx, user_response):
    if concentration_ctx["concentration_score"] is None:
        raise ValueError("Missing concentration_score in concentration analysis JSON")
    if user_response["self_focus_score"] is None:
        raise ValueError("Missing self_focus_score in user response JSON")

    concentration_ctx["concentration_score"] = float(concentration_ctx["concentration_score"])
    user_response["self_focus_score"] = float(user_response["self_focus_score"])

    confidence = user_response["self_report_confidence"]
    if confidence is not None:
        user_response["self_report_confidence"] = float(confidence)


def build_thoughtful_prompt(concentration_ctx, user_response):
    """Create a rigorous prompt that asks for balanced and evidence-grounded reasoning."""
    return f"""You are an educational analytics reasoning assistant.
Your task is to explain discrepancy between:
- model-derived concentration assessment, and
- user self-reported study experience.

IMPORTANT REASONING PRINCIPLES:
1) Be evidence-grounded: only use facts from provided inputs.
2) Be balanced: avoid assuming model is always right or user is always right.
3) Address both observable signals (physiology/environment/movement) and subjective factors (mental fatigue, cognitive load, motivation, emotional state).
4) Distinguish plausible inference from certainty; explicitly state uncertainty where needed.
5) Produce practical, student-centered recommendations.
6) Do not include chain-of-thought. Provide concise conclusions only.

Input A - Concentration Analysis (already summarized, no raw sensor sequence):
{json.dumps(concentration_ctx, ensure_ascii=False, indent=2)}

Input B - User Response:
{json.dumps(user_response, ensure_ascii=False, indent=2)}

Deliverables:
- Quantify disagreement in an interpretable way.
- Explain likely discrepancy drivers from multiple angles:
  a) measurement limitations,
  b) model blind spots,
  c) subjective perception effects,
  d) context factors not fully captured.
- Provide follow-up questions that would reduce uncertainty next time.
- Provide concrete next-step actions for both data collection and study habit improvement.

Return STRICT JSON only (no markdown, no extra text) using this exact schema:
{{
  "discrepancy_overview": {{
    "model_score": number,
    "user_score": number,
    "score_gap": number,
    "gap_interpretation": "LOW|MODERATE|HIGH",
    "one_sentence_summary": "..."
  }},
  "reasoning": {{
    "most_likely_causes": ["...", "...", "..."],
    "model_side_explanation": ["...", "..."],
    "user_side_explanation": ["...", "..."],
    "uncertainties": ["...", "..."]
  }},
  "reliability_judgment": {{
    "model_reliability": "LOW|MEDIUM|HIGH",
    "user_report_reliability": "LOW|MEDIUM|HIGH",
    "confidence_in_assessment": "LOW|MEDIUM|HIGH",
    "why": "..."
  }},
  "recommendations": {{
    "immediate_actions": ["...", "...", "..."],
    "next_session_data_to_collect": ["...", "..."],
    "follow_up_questions_for_student": ["...", "..."]
  }}
}}
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


def score_gap_label(gap):
    if gap >= 3.0:
        return "HIGH"
    if gap >= 1.5:
        return "MODERATE"
    return "LOW"


def analyze_with_llm_only(session, model, concentration_ctx, user_response):
    prompt = build_thoughtful_prompt(concentration_ctx, user_response)
    raw_response = run_cortex_complete(session=session, model=model, prompt=prompt)

    parsed = extract_json_object(raw_response)
    if parsed is None:
        score_gap = abs(concentration_ctx["concentration_score"] - user_response["self_focus_score"])
        parsed = {
            "discrepancy_overview": {
                "model_score": concentration_ctx["concentration_score"],
                "user_score": user_response["self_focus_score"],
                "score_gap": round(score_gap, 3),
                "gap_interpretation": score_gap_label(score_gap),
                "one_sentence_summary": "LLM response was not valid JSON; review raw_response for details.",
            },
            "reasoning": {
                "most_likely_causes": [],
                "model_side_explanation": [],
                "user_side_explanation": [],
                "uncertainties": ["LLM output parsing failed."],
            },
            "reliability_judgment": {
                "model_reliability": "MEDIUM",
                "user_report_reliability": "MEDIUM",
                "confidence_in_assessment": "LOW",
                "why": "Could not parse structured JSON from model output.",
            },
            "recommendations": {
                "immediate_actions": [],
                "next_session_data_to_collect": [],
                "follow_up_questions_for_student": [],
            },
        }

    return parsed, raw_response, prompt


def append_output_history(output_file: Path, output_payload: dict):
    """Append one run to a readable JSON history file without losing prior runs."""
    runs = []

    if output_file.exists():
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                existing = json.load(f)

            if isinstance(existing, list):
                runs = existing
            elif isinstance(existing, dict) and isinstance(existing.get("runs"), list):
                runs = existing["runs"]
            elif isinstance(existing, dict):
                # Backward compatibility for the old single-run file format.
                runs = [existing]
        except Exception:
            # If existing output is invalid JSON, start a new history container.
            runs = []

    runs.append(output_payload)

    history_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_runs": len(runs),
        "runs": runs,
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(history_payload, f, indent=2, ensure_ascii=False)


def process_discrepancy_reasoning(
    concentration_path,
    user_response_path,
    output_path,
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
        structured_result, raw_response, prompt_used = analyze_with_llm_only(
            session=session,
            model=model,
            concentration_ctx=concentration_ctx,
            user_response=user_response,
        )

        output_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "StudySessionDiscrepancyReasoningLLMOnly_SingleOutput",
            "model": model,
            "input": {
                "concentration_path": concentration_path,
                "user_response_path": user_response_path,
                "concentration_context": concentration_ctx,
                "user_response": user_response,
            },
            # PRIMARY OUTPUT: Discrepancy reasoning (machine-readable for DB)
            # Used immediately to understand score mismatch
            "discrepancy_reasoning": structured_result,

            "raw_llm_response": raw_response,
            "prompt_used": prompt_used,
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

    discrepancy_reasoning = output_payload["discrepancy_reasoning"]
    discrepancy_overview = discrepancy_reasoning.get("discrepancy_overview", {})

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
                        discrepancy_overview.get("score_gap"),
                        json.dumps(discrepancy_reasoning, ensure_ascii=False),
                        output_payload.get("raw_llm_response"),
                        output_payload.get("prompt_used"),
                    ),
                )
                row = cursor.fetchone()
                discrepancy_id = row[0]
                return discrepancy_id
    finally:
        connection.close()


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
        model=args.model,
        store_to_db=not args.no_store_db,
        db_user_id=args.db_user_id,
        db_device_id=args.db_device_id,
        db_session_id=args.db_session_id,
    )
