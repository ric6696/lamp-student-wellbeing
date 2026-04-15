#!/usr/bin/env python3
"""Run a 10-session FocusLLM pipeline without modifying existing project scripts.

What this script does:
1) Select first N sessions from focusllm_user1_analysis_input.json (default: 10).
2) Run CCoT concentration analysis for each selected session.
3) Generate one sample user_response JSON per session based on concentration signals.
4) Run discrepancy analysis per session and store results to Postgres.
5) Save per-session and aggregate JSON outputs.
"""

import argparse
import json
import os
import random
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from snowflake.snowpark import Session

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from llm.CCoT.analyze_study_concentration import (  # noqa: E402
    analyze_concentration,
    choose_target_session,
    compute_session_features,
)
from llm.StudySessionAnalyst.analyst import (  # noqa: E402
    analyze_with_llm_only,
    extract_concentration_context,
    extract_user_reflection,
    save_discrepancy_to_postgres,
    validate_inputs,
)


ACTIVITIES = ["Study", "Lecture", "Group Meeting", "Reading", "Writing / Report Work"]
ENVIRONMENTS = ["Library", "Classroom", "Cafe", "Home", "Outdoor"]
MENTAL_STATES = ["Very Low", "Low", "Neutral", "High", "Very High"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _build_session_payload(full_payload: dict, session_id: str) -> dict:
    sid = str(session_id)
    return {
        "sessions": [s for s in (full_payload.get("sessions") or []) if str(s.get("id")) == sid],
        "audio_events": [r for r in (full_payload.get("audio_events") or []) if str(r.get("session_id")) == sid],
        "gps": [r for r in (full_payload.get("gps") or []) if str(r.get("session_id")) == sid],
        "motion_events": [r for r in (full_payload.get("motion_events") or []) if str(r.get("session_id")) == sid],
        "vitals": [r for r in (full_payload.get("vitals") or []) if str(r.get("session_id")) == sid],
        "events": [r for r in (full_payload.get("events") or []) if str(r.get("session_id")) == sid],
    }


def _to_int_score(value, default=6) -> int:
    try:
        score = int(round(float(value)))
        return max(1, min(10, score))
    except Exception:
        return default


def _build_user_response_payload(concentration_score: int, concentration_record: dict) -> dict:
    score = _to_int_score(concentration_score)

    sensor_features = concentration_record.get("sensor_features") or {}
    audio = sensor_features.get("audio") or {}
    gps = sensor_features.get("gps") or {}
    motion = sensor_features.get("motion") or {}

    noisy = (audio.get("avg_db_conf_gt_50") or 0) > 60
    moved = bool(gps.get("drastic_location_change")) or (motion.get("active_count") or 0) > 0

    if score <= 3:
        user_score = min(10, score + 3)
        factors = [
            "Movement / Restlessness",
            "Location Change / Transition",
            "Physiological Strain (stress, fatigue, discomfort)",
        ]
    elif score <= 6:
        user_score = min(10, score + 1)
        factors = [
            "Internal Cognitive Drift (mind wandering, low motivation)",
            "Location Change / Transition",
            "Task Challenge (difficulty, frustration)",
        ]
    else:
        user_score = max(1, score - 1)
        factors = [
            "No Major Distraction",
            "Internal Cognitive Drift (mind wandering, low motivation)",
        ]

    if noisy and "Environmental Noise / Speech" not in factors:
        factors = ["Environmental Noise / Speech"] + factors[:2]
    elif moved and "Location Change / Transition" not in factors:
        factors = ["Location Change / Transition"] + factors[:2]

    return {
        "user_response": [
            {
                "question": "How focused were you during this study session?",
                "answer": user_score,
            },
            {
                "question": "What most affected your concentration during this session?",
                "answer": factors,
            },
        ]
    }


def _build_pre_session_payload(base_payload: dict, concentration_record: dict) -> dict:
    return {
        "activity_context": random.choice(ACTIVITIES),
        "environment_context": random.choice(ENVIRONMENTS),
        "mental_readiness": random.choice(MENTAL_STATES),
    }


def run_pipeline(sample_count: int, model: str, store_to_db: bool, db_user_id: str | None) -> int:
    load_dotenv(repo_root / ".env")

    input_path = repo_root / "llm" / "CCoT" / "output" / "focusllm_user1_analysis_input.json"
    pre_session_path = repo_root / "llm" / "CCoT" / "output" / "pre_session_context.json"
    if not input_path.exists():
        raise FileNotFoundError(f"Missing input file: {input_path}")
    if not pre_session_path.exists():
        raise FileNotFoundError(f"Missing pre-session context file: {pre_session_path}")

    full_payload = _read_json(input_path)
    base_pre_session_payload = _read_json(pre_session_path)
    sessions = full_payload.get("sessions") or []
    selected_sessions = sessions[:sample_count]
    if not selected_sessions:
        raise RuntimeError("No sessions found in focusllm_user1_analysis_input.json")

    selected_ids = [str(s.get("id")) for s in selected_sessions]

    ccot_dir = repo_root / "llm" / "CCoT" / "output" / "sample_10"
    user_dir = repo_root / "llm" / "StudySessionAnalyst" / "sample_10_user_response"
    discrepancy_dir = repo_root / "llm" / "StudySessionAnalyst" / "sample_10_discrepancy"
    pre_session_dir = repo_root / "llm" / "StudySessionAnalyst" / "sample_10_pre_session"

    ccot_dir.mkdir(parents=True, exist_ok=True)
    user_dir.mkdir(parents=True, exist_ok=True)
    discrepancy_dir.mkdir(parents=True, exist_ok=True)
    pre_session_dir.mkdir(parents=True, exist_ok=True)

    connection_params = {
        "account": os.environ.get("SNOWFLAKE_ACCOUNT"),
        "user": os.environ.get("SNOWFLAKE_USER"),
        "password": os.environ.get("SNOWFLAKE_USER_PASSWORD"),
        "role": os.environ.get("SNOWFLAKE_ROLE"),
        "database": os.environ.get("SNOWFLAKE_DATABASE"),
        "schema": os.environ.get("SNOWFLAKE_SCHEMA"),
        "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE"),
    }

    snowflake_session = Session.builder.configs(connection_params).create()

    concentration_rows = []
    discrepancy_rows = []
    errors = []

    try:
        total = len(selected_ids)
        for idx, sid in enumerate(selected_ids, 1):
            print(f"[{idx}/{total}] Session {sid}")
            try:
                single_payload = _build_session_payload(full_payload, sid)
                target_session = choose_target_session(single_payload)
                features = compute_session_features(single_payload, target_session)

                concentration_result = analyze_concentration(
                    features=features,
                    model=model,
                    session=snowflake_session,
                )

                concentration_record = {
                    "timestamp": _now_iso(),
                    "model": model,
                    "source": "json_batch",
                    "session": features.get("session") or {},
                    "sensor_features": features,
                    "phase_1": concentration_result.get("phase_1") or {},
                    "phase_2": concentration_result.get("phase_2") or {},
                    "phase_3": concentration_result.get("phase_3") or {},
                    "personalization_basis": concentration_result.get("personalization_basis"),
                    "score": concentration_result.get("score"),
                    "reason": concentration_result.get("reason") or "",
                    "error": concentration_result.get("error"),
                }

                concentration_rows.append(concentration_record)
                ccot_file = ccot_dir / f"{sid}_concentration.json"
                _write_json(ccot_file, concentration_record)

                user_response_payload = _build_user_response_payload(
                    concentration_score=_to_int_score(concentration_record.get("score")),
                    concentration_record=concentration_record,
                )
                user_file = user_dir / f"{sid}_user_response.json"
                _write_json(user_file, user_response_payload)

                pre_session_payload = _build_pre_session_payload(
                    base_payload=base_pre_session_payload,
                    concentration_record=concentration_record,
                )
                pre_session_file = pre_session_dir / f"{sid}_pre_session.json"
                _write_json(pre_session_file, pre_session_payload)

                concentration_ctx = extract_concentration_context(deepcopy(concentration_record))
                user_response = extract_user_reflection(deepcopy(user_response_payload))
                validate_inputs(concentration_ctx, user_response)

                discrepancy_structured = analyze_with_llm_only(
                    session=snowflake_session,
                    model=model,
                    concentration_ctx=concentration_ctx,
                    user_response=user_response,
                )

                db_id = None
                if store_to_db:
                    save_payload = {
                        "timestamp": _now_iso(),
                        "model": model,
                        "pre_session_questions": pre_session_payload,
                        "result": discrepancy_structured,
                    }
                    db_id = save_discrepancy_to_postgres(
                        output_payload=save_payload,
                        user_id=(db_user_id or (features.get("session") or {}).get("user_id") or "minsuk"),
                        device_id=(features.get("session") or {}).get("device_id"),
                        session_id=None,
                    )

                discrepancy_record = {
                    "timestamp": _now_iso(),
                    "model": model,
                    "session_id": sid,
                    "concentration_score": concentration_ctx.get("score"),
                    "user_score": user_response.get("self_focus_score"),
                    "selected_factors": user_response.get("selected_factors"),
                    "pre_session_questions": pre_session_payload,
                    "result": discrepancy_structured,
                    "db_row_id": db_id,
                }
                discrepancy_rows.append(discrepancy_record)

                discrepancy_file = discrepancy_dir / f"{sid}_discrepancy.json"
                _write_json(discrepancy_file, discrepancy_record)

            except Exception as exc:
                errors.append({"session_id": sid, "error": str(exc)})

    finally:
        snowflake_session.close()

    concentration_aggregate = {
        "generated_at": _now_iso(),
        "model": model,
        "session_count": len(selected_ids),
        "completed_count": len([r for r in concentration_rows if r.get("score") is not None]),
        "error_count": len([r for r in concentration_rows if r.get("score") is None]) + len(errors),
        "errors": errors,
        "results": concentration_rows,
    }

    user_response_aggregate = {
        "generated_at": _now_iso(),
        "session_count": len(selected_ids),
        "results": [
            {
                "session_id": sid,
                "file": str((user_dir / f"{sid}_user_response.json").relative_to(repo_root)),
                "pre_session_file": str((pre_session_dir / f"{sid}_pre_session.json").relative_to(repo_root)),
            }
            for sid in selected_ids
        ],
    }

    discrepancy_aggregate = {
        "generated_at": _now_iso(),
        "model": model,
        "session_count": len(selected_ids),
        "completed_count": len(discrepancy_rows),
        "error_count": len(selected_ids) - len(discrepancy_rows),
        "errors": errors,
        "results": discrepancy_rows,
    }

    _write_json(
        repo_root / "llm" / "CCoT" / "output" / "focusllm_user1_concentration_10_sessions.json",
        concentration_aggregate,
    )
    _write_json(
        repo_root / "llm" / "StudySessionAnalyst" / "focusllm_user1_user_response_10_sessions.json",
        user_response_aggregate,
    )
    _write_json(
        repo_root / "llm" / "StudySessionAnalyst" / "focusllm_user1_discrepancy_10_sessions.json",
        discrepancy_aggregate,
    )

    print("\nPipeline finished.")
    print(f"Selected sessions: {len(selected_ids)}")
    print(f"Concentration completed: {concentration_aggregate['completed_count']}")
    print(f"Discrepancy completed: {discrepancy_aggregate['completed_count']}")
    print(f"Errors: {len(errors)}")

    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 10-sample FocusLLM concentration + discrepancy pipeline")
    parser.add_argument("--count", type=int, default=10, help="Number of sessions to process (default: 10)")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-5", help="Snowflake Cortex model")
    parser.add_argument(
        "--no-store-db",
        action="store_true",
        help="Disable DB inserts for discrepancy results",
    )
    parser.add_argument(
        "--db-user-id",
        type=str,
        default=os.getenv("ANALYST_DEFAULT_USER_ID", "minsuk"),
        help="User ID used for DB inserts",
    )

    args = parser.parse_args()
    return run_pipeline(
        sample_count=max(1, args.count),
        model=args.model,
        store_to_db=not args.no_store_db,
        db_user_id=args.db_user_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
