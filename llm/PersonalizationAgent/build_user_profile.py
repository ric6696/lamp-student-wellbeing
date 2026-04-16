import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from snowflake.snowpark import Session
from llm.PersonalizationAgent.context_averages import (
	build_context_average_concentration_scores,
)

try:
	import psycopg2
	import psycopg2.extras
except ImportError:  # pragma: no cover
	psycopg2 = None


SENSOR_KEYS = ("vitals", "gps", "motion", "audio")
DEFAULT_OUTPUT_PATH = SCRIPT_DIR / "user_profile_summary.json"
DEFAULT_DISCREPANCY_READ_LIMIT = int(os.getenv("PERSONALIZATION_DISCREPANCY_READ_LIMIT", "60"))


def _normalize_read_limit(limit):
	val = int(_to_float(limit) or 0)
	# 0 or negative means read all available discrepancy rows.
	return None if val <= 0 else val

def _to_float(value):
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _to_text(value):
	if value is None:
		return ""
	if isinstance(value, list):
		return " ".join(str(v) for v in value)
	if isinstance(value, dict):
		return " ".join(str(v) for v in value.values())
	return str(value)


def get_db_connection():
	if psycopg2 is None:
		raise RuntimeError(
			"psycopg2 is not installed. Install with `pip install psycopg2-binary` in your active environment."
		)

	return psycopg2.connect(
		host=os.getenv("POSTGRES_HOST", "localhost"),
		port=int(os.getenv("POSTGRES_PORT", "5433")),
		dbname=os.getenv("POSTGRES_DB", "sensing_db"),
		user=os.getenv("POSTGRES_USER", "postgres"),
		password=os.getenv("POSTGRES_PASSWORD", "dev_password"),
	)


def get_snowflake_session():
	# Reuse the same env naming convention already used by analyst.py.
	params = {
		"account": os.getenv("SNOWFLAKE_ACCOUNT"),
		"user": os.getenv("SNOWFLAKE_USER"),
		"password": os.getenv("SNOWFLAKE_USER_PASSWORD"),
		"role": os.getenv("SNOWFLAKE_ROLE"),
		"database": os.getenv("SNOWFLAKE_DATABASE"),
		"schema": os.getenv("SNOWFLAKE_SCHEMA"),
		"warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
	}
	missing = [k for k, v in params.items() if not v]
	if missing:
		raise RuntimeError("Missing Snowflake env vars: " + ", ".join(missing))
	return Session.builder.configs(params).create()


def assert_profile_table_exists(connection):
	# DB schema must be created via SQL migrations under database/init.
	query = """
		SELECT EXISTS (
			SELECT 1
			FROM information_schema.tables
			WHERE table_schema = current_schema()
			  AND table_name = 'user_personalization_profiles'
		)
	"""
	with connection.cursor() as cursor:
		cursor.execute(query)
		row = cursor.fetchone()
		exists = bool(row[0]) if row else False

	if not exists:
		raise RuntimeError(
			"Table user_personalization_profiles does not exist in current schema. "
			"Run database/init/04_user_personalization_profiles.sql first."
		)


def ensure_profile_tracking_columns(connection):
	query = """
		ALTER TABLE user_personalization_profiles
		ADD COLUMN IF NOT EXISTS data_fed_count INTEGER NOT NULL DEFAULT 0,
		ADD COLUMN IF NOT EXISTS profile_update_count INTEGER NOT NULL DEFAULT 0
	"""
	with connection.cursor() as cursor:
		cursor.execute(query)
	connection.commit()


def fetch_existing_profile_state(connection, user_id):
	query = """
		SELECT profile_update_count, data_fed_count
		FROM user_personalization_profiles
		WHERE user_id = %s
	"""
	with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
		cursor.execute(query, (user_id.lower(),))
		return cursor.fetchone() or {}


def fetch_latest_discrepancy_row(connection, user_id):
	query = """
		SELECT
			id,
			created_at,
			user_id,
			model_name,
			session_id,
			device_id,
			model_score,
			user_score,
			score_gap,
			discrepancy_reasoning
		FROM session_discrepancy_analyses
		WHERE user_id = %s
		ORDER BY created_at DESC
		LIMIT 1
	"""

	with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
		cursor.execute(query, (user_id.lower(),))
		return cursor.fetchone()


def fetch_recent_discrepancy_rows(connection, user_id, limit=None):
	limit_sql = "LIMIT %s" if limit is not None else ""
	query = """
		SELECT
			id,
			created_at,
			user_id,
			model_name,
			session_id,
			device_id,
			model_score,
			user_score,
			score_gap,
			pre_session_questions,
			discrepancy_reasoning
		FROM session_discrepancy_analyses
		WHERE user_id = %s
		ORDER BY created_at DESC
		{limit_sql}
	"""

	with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
		params = (user_id.lower(),) if limit is None else (user_id.lower(), limit)
		cursor.execute(query.format(limit_sql=limit_sql), params)
		return cursor.fetchall()


def run_cortex_complete(session, model, prompt):
	# Escape single quotes for SQL string literal safety.
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
	stripped = (text or "").strip()
	if not stripped:
		return None

	try:
		return json.loads(stripped)
	except Exception:
		pass

	first = stripped.find("{")
	last = stripped.rfind("}")
	if first != -1 and last != -1 and first < last:
		try:
			return json.loads(stripped[first : last + 1])
		except Exception:
			return None

	return None


def _normalize_level(value, allowed, default):
	if value is None:
		return default
	value = str(value).strip().upper()
	return value if value in allowed else default


def _default_sensor_guide(reason):
	# Always return valid schema so DB upsert is resilient.
	out = {}
	for sensor in SENSOR_KEYS:
		out[sensor] = {
			"importance": "LOW",
			"confidence": "LOW",
			"summary": f"Fallback used: {reason}",
			"evidence": {
				"sessions_seen": 0,
				"supported_count": 0,
				"contradicted_count": 0,
				"unclear_count": 0,
				"avg_gap_when_flagged": None,
			},
			"interpretation": {
				"what_it_means": f"Fallback used: {reason}",
				"risk": "History-based sensor interpretation could not be completed.",
				"next_use": "Insufficient history to assess this sensor reliably.",
			},
		}
	return out


def _validate_sensor_guide(parsed):
	if not isinstance(parsed, dict):
		return None

	validated = {}
	for sensor in SENSOR_KEYS:
		item = parsed.get(sensor)
		if not isinstance(item, dict):
			return None

		evidence = item.get("evidence")
		if not isinstance(evidence, dict):
			return None

		interpretation = item.get("interpretation")
		if not isinstance(interpretation, dict):
			return None

		validated[sensor] = {
			"importance": _normalize_level(item.get("importance"), {"LOW", "MEDIUM", "HIGH"}, "LOW"),
			"confidence": _normalize_level(item.get("confidence"), {"LOW", "MEDIUM", "HIGH"}, "LOW"),
			"summary": _to_text(item.get("summary")).strip() or "No summary provided.",
			"evidence": {
				"sessions_seen": int(_to_float(evidence.get("sessions_seen")) or 0),
				"supported_count": int(_to_float(evidence.get("supported_count")) or 0),
				"contradicted_count": int(_to_float(evidence.get("contradicted_count")) or 0),
				"unclear_count": int(_to_float(evidence.get("unclear_count")) or 0),
				"avg_gap_when_flagged": _to_float(evidence.get("avg_gap_when_flagged")),
			},
			"interpretation": {
				"what_it_means": _to_text(interpretation.get("what_it_means")).strip()
				or "No interpretation provided.",
				"risk": _to_text(interpretation.get("risk")).strip() or "No risk provided.",
				"next_use": _to_text(interpretation.get("next_use")).strip()
				or "Insufficient history to assess this sensor reliably.",
			},
		}

	return validated


def _serialize_discrepancy_history(rows):
	history = []
	for row in rows or []:
		history.append(
			{
				"created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
				"model_name": row.get("model_name"),
				"session_id": row.get("session_id"),
				"model_score": _to_float(row.get("model_score")),
				"user_score": _to_float(row.get("user_score")),
				"score_gap": _to_float(row.get("score_gap")),
				"pre_session_questions": row.get("pre_session_questions") or {},
				"discrepancy_reasoning": row.get("discrepancy_reasoning") or {},
			}
		)
	return history


def build_sensor_prompt(user_id, rows):
	input_payload = {
		"user_id": user_id,
		"sample_size": len(rows or []),
		"discrepancy_history": _serialize_discrepancy_history(rows),
	}

	return f"""You are a learning analytics personalization assistant.
Analyze the user's full discrepancy history and produce a sensor profile for next-session concentration interpretation.

Requirements:
1) Use the whole discrepancy history, not just one session.
2) Base conclusions on recurring evidence patterns from the stored discrepancy analyses.
3) Do not invent numeric thresholds or behavioral rules that are not supported by the history.
4) Be conservative when evidence is sparse or mixed.
5) Return strict JSON only.

Input:
{json.dumps(input_payload, ensure_ascii=False, indent=2)}

Return exactly this shape:
{{
  "vitals": {{
    "importance": "LOW|MEDIUM|HIGH",
    "confidence": "LOW|MEDIUM|HIGH",
    "summary": "...",
    "evidence": {{
      "sessions_seen": number,
      "supported_count": number,
      "contradicted_count": number,
      "unclear_count": number,
      "avg_gap_when_flagged": number | null
    }},
    "interpretation": {{
      "what_it_means": "...",
      "risk": "...",
      "next_use": "..."
    }}
  }},
  "gps": {{ "...": "same shape as vitals" }},
  "motion": {{ "...": "same shape as vitals" }},
  "audio": {{ "...": "same shape as vitals" }}
}}

Guidance for evidence counts:
- `sessions_seen`: sessions in the provided history where this sensor is meaningfully discussed or used as evidence.
- `supported_count`: sessions where the discrepancy reasoning suggests this sensor aligned well with the user's reported focus/distraction.
- `contradicted_count`: sessions where the discrepancy reasoning suggests this sensor was misleading or over-weighted.
- `unclear_count`: sessions where evidence was too weak or ambiguous.
- `avg_gap_when_flagged`: average of (user_score - model_score) for sessions where this sensor was meaningfully involved; null if unavailable.
"""


def evaluate_sensors_with_cortex(session, model, user_id, rows):
	prompt = build_sensor_prompt(user_id, rows)
	raw_response = run_cortex_complete(session=session, model=model, prompt=prompt)
	parsed = extract_json_object(raw_response)
	validated = _validate_sensor_guide(parsed)
	if validated is None:
		return _default_sensor_guide("Cortex response was not valid JSON schema."), raw_response, prompt
	return validated, raw_response, prompt


def build_calibration_text(rows):
	scored_rows = []
	for row in rows or []:
		model_score = _to_float(row.get("model_score"))
		user_score = _to_float(row.get("user_score"))
		if model_score is None or user_score is None:
			continue
		scored_rows.append(
			{
				"diff": user_score - model_score,
				"abs_gap": abs(user_score - model_score),
			}
		)

	if not scored_rows:
		return {
			"alignment_summary": "Insufficient scoring data to determine user-model alignment.",
			"why": "No discrepancy rows with both model_score and user_score were available.",
			"discrepancy_strength": "Gap size could not be summarized from the available history.",
			"ccot_adjustment_recommendation": (
				"Use conservative personalization and rely on sensor-specific evidence until more scored sessions are available."
			),
		}

	positive_count = sum(1 for item in scored_rows if item["diff"] > 0)
	negative_count = sum(1 for item in scored_rows if item["diff"] < 0)
	zero_count = sum(1 for item in scored_rows if item["diff"] == 0)
	avg_diff = round(sum(item["diff"] for item in scored_rows) / len(scored_rows), 3)
	avg_abs_gap = round(sum(item["abs_gap"] for item in scored_rows) / len(scored_rows), 3)

	if positive_count > negative_count:
		alignment_text = "Across the history, the user more often rates focus higher than the model."
		ccot_text = (
			"For next-session interpretation, avoid overly strict downgrades and check whether strong negative signals are consistently supported by other sensors."
		)
	elif negative_count > positive_count:
		alignment_text = "Across the history, the user more often rates focus lower than the model."
		ccot_text = (
			"For next-session interpretation, be cautious with optimistic model outputs and look for stronger confirmation before concluding high focus."
		)
	else:
		alignment_text = "Across the history, user and model do not show a strong one-direction bias."
		ccot_text = (
			"For next-session interpretation, keep bias correction light and prioritize sensor-specific reliability patterns."
		)

	why_text = (
		f"Based on {len(scored_rows)} scored sessions, average (user_score - model_score) = {avg_diff}, "
		f"with {positive_count} positive, {negative_count} negative, and {zero_count} zero-gap directions."
	)
	gap_text = f"Average absolute model-user gap across scored history: {avg_abs_gap}."

	return {
		"alignment_summary": alignment_text,
		"why": why_text,
		"discrepancy_strength": gap_text,
		"ccot_adjustment_recommendation": ccot_text,
	}
def build_profile_from_latest(
	user_id,
	row,
	all_rows=None,
	snowflake_session=None,
	cortex_model=None,
	existing_profile=None,
):
	now = datetime.now(timezone.utc).isoformat()
	existing_profile = existing_profile or {}
	current_update_count = int(_to_float(existing_profile.get("profile_update_count")) or 0)
	profile_update_count = current_update_count + 1
	if not row:
		return {
			"generated_at": now,
			"user_id": user_id,
			"sample_size": 0,
			"data_fed_count": 0,
			"profile_update_count": profile_update_count,
			"profile_confidence": 0.0,
			"summary": "No discrepancy analysis found for this user.",
			"source": {},
			"calibration": {},
			"context_average_concentration_scores": build_context_average_concentration_scores(
				all_rows or []
			),
			"sensor_evaluation_guide": {},
		}

	history_rows = all_rows or [row]
	history_count = len(history_rows)
	scored_history_count = sum(
		1
		for item in history_rows
		if _to_float(item.get("model_score")) is not None and _to_float(item.get("user_score")) is not None
	)
	profile_confidence = round(scored_history_count / history_count, 3) if history_count else 0.0

	model_name = cortex_model or os.getenv("PERSONALIZATION_CORTEX_MODEL", "claude-sonnet-4-5")
	sensor_eval_source = "snowflake_cortex"

	if snowflake_session is None:
		sensor_guide = _default_sensor_guide("Snowflake session unavailable.")
		sensor_eval_source = "fallback_no_snowflake"
	else:
		try:
			sensor_guide, _, _ = evaluate_sensors_with_cortex(
				session=snowflake_session,
				model=model_name,
				user_id=user_id,
				rows=history_rows,
			)
		except Exception as exc:
			sensor_guide = _default_sensor_guide(f"Snowflake Cortex error: {exc}")
			sensor_eval_source = "fallback_cortex_error"

	calibration = build_calibration_text(history_rows)
	context_averages = build_context_average_concentration_scores(history_rows)

	# Compute session range from discrepancy analysis IDs
	analysis_ids = [row.get("id") for row in history_rows if row.get("id")]
	session_range = f"Analysis {min(analysis_ids)}-{max(analysis_ids)}" if analysis_ids else "Unknown"

	return {
		"generated_at": now,
		"user_id": user_id,
		"sample_size": history_count,
		"data_fed_count": history_count,
		"profile_update_count": profile_update_count,
		"sessions_analyzed": session_range,
		"profile_confidence": profile_confidence,
		"calibration": calibration,
		"sensor_interpretation": sensor_guide,
	}


def upsert_profile_to_db(connection, user_id, payload, context_averages=None, latest_row=None):
	query = """
		INSERT INTO user_personalization_profiles (
			user_id,
			source_discrepancy_analysis_id,
			profile_confidence,
			data_fed_count,
			profile_update_count,
			mental_readiness_averages,
			activity_context_averages,
			environment_context_averages,
			profile_payload,
			updated_at
		) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, NOW())
		ON CONFLICT (user_id)
		DO UPDATE SET
			source_discrepancy_analysis_id = EXCLUDED.source_discrepancy_analysis_id,
			profile_confidence = EXCLUDED.profile_confidence,
			data_fed_count = EXCLUDED.data_fed_count,
			profile_update_count = EXCLUDED.profile_update_count,
			mental_readiness_averages = EXCLUDED.mental_readiness_averages,
			activity_context_averages = EXCLUDED.activity_context_averages,
			environment_context_averages = EXCLUDED.environment_context_averages,
			profile_payload = EXCLUDED.profile_payload,
			updated_at = NOW()
	"""

	context_averages = context_averages or {}
	latest_analysis_id = latest_row.get("id") if latest_row else None
	
	with connection.cursor() as cursor:
		cursor.execute(
			query,
			(
				user_id.lower(),
				latest_analysis_id,
				payload.get("profile_confidence"),
				payload.get("data_fed_count"),
				payload.get("profile_update_count"),
				json.dumps(context_averages.get("mental_readiness", {}), ensure_ascii=False),
				json.dumps(context_averages.get("activity_context", {}), ensure_ascii=False),
				json.dumps(context_averages.get("environment_context", {}), ensure_ascii=False),
				json.dumps(payload, ensure_ascii=False),
			),
		)
	connection.commit()


def refresh_user_profile(
	connection,
	user_id,
	output_path=DEFAULT_OUTPUT_PATH,
	snowflake_session=None,
	store_db=True,
	discrepancy_read_limit=DEFAULT_DISCREPANCY_READ_LIMIT,
):
	assert_profile_table_exists(connection)
	ensure_profile_tracking_columns(connection)
	all_rows = fetch_recent_discrepancy_rows(
		connection,
		user_id,
		limit=_normalize_read_limit(discrepancy_read_limit),
	)
	row = all_rows[0] if all_rows else None
	existing_profile = fetch_existing_profile_state(connection, user_id)
	payload = build_profile_from_latest(
		user_id=user_id,
		row=row,
		all_rows=all_rows,
		snowflake_session=snowflake_session,
		existing_profile=existing_profile,
	)

	# Build context averages separately for DB storage
	context_averages = build_context_average_concentration_scores(all_rows or [])

	saved_file = save_profile(output_path, payload)

	if store_db:
		upsert_profile_to_db(connection, user_id, payload, context_averages, row)

	return saved_file, payload


def save_profile(output_path, payload):
	output_file = Path(output_path)
	if not output_file.is_absolute():
		output_file = SCRIPT_DIR / output_file
	output_file.parent.mkdir(parents=True, exist_ok=True)
	with open(output_file, "w", encoding="utf-8") as f:
		json.dump(payload, f, indent=2, ensure_ascii=False)
	return output_file


def main(
	user_id="minsuk",
	output_path=str(DEFAULT_OUTPUT_PATH),
	store_db=True,
	discrepancy_read_limit=DEFAULT_DISCREPANCY_READ_LIMIT,
):
	user_id = (user_id or "").strip()
	if not user_id:
		user_id = input("Enter user ID: ").strip()
	if not user_id:
		raise SystemExit("Error: user ID is required.")

	load_dotenv()
	pg_connection = get_db_connection()
	sf_session = None

	# Snowflake is optional at runtime; fallback output is generated if unavailable.
	try:
		sf_session = get_snowflake_session()
	except Exception as exc:
		print(f"Snowflake unavailable. Using fallback sensor evaluation: {exc}")

	try:
		saved_file, payload = refresh_user_profile(
			connection=pg_connection,
			user_id=user_id,
			output_path=output_path,
			snowflake_session=sf_session,
			store_db=store_db,
			discrepancy_read_limit=discrepancy_read_limit,
		)
	finally:
		if sf_session is not None:
			sf_session.close()
		pg_connection.close()

	print(f"User profile saved: {saved_file}")
	print(
		f"user_id={user_id}, sample_size={payload['sample_size']}, confidence={payload['profile_confidence']}"
	)


if __name__ == "__main__":
	main()
