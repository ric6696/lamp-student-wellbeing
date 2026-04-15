#!/usr/bin/env python3
"""Run concentration and discrepancy reasoning for all sessions in synthesized FocusLLM data."""

import json
import os
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
    extract_user_reflection,
    validate_inputs,
    extract_concentration_context,
)


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def _build_session_payload(full_payload: dict, session_id: str) -> dict:
    return {
        "sessions": [s for s in (full_payload.get("sessions") or []) if str(s.get("id")) == str(session_id)],
        "audio_events": [r for r in (full_payload.get("audio_events") or []) if str(r.get("session_id")) == str(session_id)],
        "gps": [r for r in (full_payload.get("gps") or []) if str(r.get("session_id")) == str(session_id)],
        "motion_events": [r for r in (full_payload.get("motion_events") or []) if str(r.get("session_id")) == str(session_id)],
        "vitals": [r for r in (full_payload.get("vitals") or []) if str(r.get("session_id")) == str(session_id)],
        "events": [r for r in (full_payload.get("events") or []) if str(r.get("session_id")) == str(session_id)],
    }


def _build_concentration_record(model: str, features: dict, result: dict) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model": model,
        "source": "json_batch",
        "session": features["session"],
        "sensor_features": features,
        "phase_1": result.get("phase_1") or {},
        "phase_2": result.get("phase_2") or {},
        "phase_3": result.get("phase_3") or {},
        "personalization_basis": result.get("personalization_basis"),
        "score": result.get("score"),
        "reason": result.get("reason") or "",
        "error": result.get("error"),
    }


def run_batch(model: str = "claude-sonnet-4-5") -> int:
    load_dotenv(repo_root / ".env")

    input_path = repo_root / "llm" / "CCoT" / "output" / "focusllm_user1_analysis_input.json"
    user_response_path = repo_root / "llm" / "StudySessionAnalyst" / "user_response_to_concentration.json"

    concentration_output = repo_root / "llm" / "CCoT" / "output" / "focusllm_user1_concentration_all_sessions.json"
    discrepancy_output = repo_root / "llm" / "StudySessionAnalyst" / "focusllm_user1_discrepancy_all_sessions.json"

    if not input_path.exists():
        raise FileNotFoundError(f"Missing analysis input: {input_path}")
    if not user_response_path.exists():
        raise FileNotFoundError(f"Missing user response: {user_response_path}")

    full_payload = _read_json(input_path)
    sessions = full_payload.get("sessions") or []
    user_payload = _read_json(user_response_path)

    if not sessions:
        raise RuntimeError("No sessions found in analysis input payload")

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
    concentration_errors = []
    discrepancy_errors = []

    try:
        total = len(sessions)
        for idx, session_meta in enumerate(sessions, 1):
            session_id = str(session_meta.get("id"))
            print(f"[{idx}/{total}] Processing {session_id}")

            single_payload = _build_session_payload(full_payload, session_id)
            target_session = choose_target_session(single_payload)
            features = compute_session_features(single_payload, target_session)

            result = analyze_concentration(features=features, model=model, session=snowflake_session)
            record = _build_concentration_record(model=model, features=features, result=result)
            concentration_rows.append(record)

            if record.get("score") is None:
                concentration_errors.append(
                    {
                        "session_id": session_id,
                        "error": record.get("error") or record.get("reason") or "Unknown concentration error",
                    }
                )
                continue

            try:
                concentration_ctx = extract_concentration_context(record)
                user_response = extract_user_reflection(deepcopy(user_payload))
                validate_inputs(concentration_ctx, user_response)

                structured_result, raw_response, prompt_used = analyze_with_llm_only(
                    session=snowflake_session,
                    model=model,
                    concentration_ctx=concentration_ctx,
                    user_response=user_response,
                )

                discrepancy_rows.append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "model": model,
                        "session_id": session_id,
                        "concentration_score": concentration_ctx["score"],
                        "user_score": user_response["self_focus_score"],
                        "selected_factors": user_response.get("selected_factors"),
                        "result": structured_result,
                        "raw_llm_response": raw_response,
                        "prompt_used": prompt_used,
                    }
                )
            except Exception as exc:
                discrepancy_errors.append(
                    {
                        "session_id": session_id,
                        "error": str(exc),
                    }
                )

    finally:
        snowflake_session.close()

    concentration_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model": model,
        "session_count": len(sessions),
        "completed_count": len([r for r in concentration_rows if r.get("score") is not None]),
        "error_count": len(concentration_errors),
        "errors": concentration_errors,
        "results": concentration_rows,
    }

    discrepancy_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model": model,
        "session_count": len(sessions),
        "completed_count": len(discrepancy_rows),
        "error_count": len(discrepancy_errors),
        "errors": discrepancy_errors,
        "results": discrepancy_rows,
    }

    _write_json(concentration_output, concentration_payload)
    _write_json(discrepancy_output, discrepancy_payload)

    print("\nDone.")
    print(f"Concentration output: {concentration_output}")
    print(f"Discrepancy output:   {discrepancy_output}")
    print(f"Concentration done: {concentration_payload['completed_count']}/{len(sessions)}")
    print(f"Discrepancy done:   {discrepancy_payload['completed_count']}/{len(sessions)}")

    return 0


if __name__ == "__main__":
    model_name = os.getenv("LLM_MODEL", "claude-sonnet-4-5")
    raise SystemExit(run_batch(model=model_name))
