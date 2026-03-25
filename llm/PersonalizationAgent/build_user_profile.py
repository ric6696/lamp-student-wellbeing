import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from snowflake.snowpark import Session

try:
	import psycopg2
	import psycopg2.extras
except ImportError:  # pragma: no cover
	psycopg2 = None


# Static policy guidance is kept deterministic; LLM fills interpretation.
SENSOR_RULES = {
	"heart_rate": {
		"guidance": "Use heart rate as supporting context only. Avoid strong conclusions from HR alone because stress/arousal can be unrelated to focus.",
	},
	"noise_level": {
		"guidance": "Use noise as a primary environment signal. Evaluate both average dB and disruptive spikes/labels.",
	},
	"steps_movement": {
		"guidance": "Interpret movement relative to task type. Penalize sustained movement during seated tasks more than brief posture changes.",
	},
	"gps_location": {
		"guidance": "Use location stability as secondary evidence. Large travel usually reduces focus reliability for session-level interpretation.",
	},
}

SENSOR_KEYS = tuple(SENSOR_RULES.keys())
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_PATH = SCRIPT_DIR / "user_profile_summary.json"

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
	for sensor, rule in SENSOR_RULES.items():
		out[sensor] = {
			"importance": "LOW",
			"confidence": "LOW",
			"rationale": f"Fallback used: {reason}",
			"how_to_evaluate": rule["guidance"],
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

		validated[sensor] = {
			"importance": _normalize_level(item.get("importance"), {"LOW", "MEDIUM", "HIGH"}, "LOW"),
			"confidence": _normalize_level(item.get("confidence"), {"LOW", "MEDIUM", "HIGH"}, "LOW"),
			"rationale": _to_text(item.get("rationale")).strip() or "No rationale provided.",
			"how_to_evaluate": _to_text(item.get("how_to_evaluate")).strip() or SENSOR_RULES[sensor]["guidance"],
		}

	return validated


def build_sensor_prompt(user_id, row, model_score, user_score, score_gap):
	# The model receives structured discrepancy context and outputs sensor evaluation JSON.
	input_payload = {
		"user_id": user_id,
		"model_name": row.get("model_name"),
		"created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
		"model_score": model_score,
		"user_score": user_score,
		"score_gap": score_gap,
		"discrepancy_reasoning": row.get("discrepancy_reasoning") or {},
		"sensor_policy": {k: v["guidance"] for k, v in SENSOR_RULES.items()},
	}

	return f"""You are a learning analytics personalization assistant.
Evaluate the usefulness of each sensor for next-session concentration interpretation.

Requirements:
1) Use semantic reasoning from context, not keyword matching.
2) If evidence is weak/missing, set LOW and explain uncertainty.
3) Be conservative with heart_rate unless evidence is explicit.
4) Return strict JSON only.

Input:
{json.dumps(input_payload, ensure_ascii=False, indent=2)}

Return exactly this shape:
{{
  "heart_rate": {{"importance": "LOW|MEDIUM|HIGH", "confidence": "LOW|MEDIUM|HIGH", "rationale": "...", "how_to_evaluate": "..."}},
  "noise_level": {{"importance": "LOW|MEDIUM|HIGH", "confidence": "LOW|MEDIUM|HIGH", "rationale": "...", "how_to_evaluate": "..."}},
  "steps_movement": {{"importance": "LOW|MEDIUM|HIGH", "confidence": "LOW|MEDIUM|HIGH", "rationale": "...", "how_to_evaluate": "..."}},
  "gps_location": {{"importance": "LOW|MEDIUM|HIGH", "confidence": "LOW|MEDIUM|HIGH", "rationale": "...", "how_to_evaluate": "..."}}
}}"""


def evaluate_sensors_with_cortex(session, model, user_id, row, model_score, user_score, score_gap):
	prompt = build_sensor_prompt(user_id, row, model_score, user_score, score_gap)
	raw_response = run_cortex_complete(session=session, model=model, prompt=prompt)
	parsed = extract_json_object(raw_response)
	validated = _validate_sensor_guide(parsed)
	if validated is None:
		return _default_sensor_guide("Cortex response was not valid JSON schema."), raw_response, prompt
	return validated, raw_response, prompt


def build_calibration_text(diff, score_gap):
	# Calibration is intentionally text-only for interpretability.
	if diff is None:
		alignment_text = "Insufficient scoring data to determine user-model alignment."
		why_text = (
			"The latest discrepancy record does not include both model_score and user_score, "
			"so direct preference direction cannot be inferred."
		)
		ccot_text = (
			"Use conservative personalization. Keep model defaults and rely more on qualitative reasoning "
			"until additional scored sessions are available."
		)
	elif diff > 0.3:
		alignment_text = "User tends to rate focus higher than the model."
		why_text = (
			"In the latest discrepancy analysis, the user self-rating is meaningfully above the model estimate, "
			"indicating the model may be slightly strict for this user."
		)
		ccot_text = (
			"For next-session interpretation, avoid overly harsh downgrades when context is mixed. "
			"Give more weight to stable environment signals before concluding low focus."
		)
	elif diff < -0.3:
		alignment_text = "User tends to rate focus lower than the model."
		why_text = (
			"In the latest discrepancy analysis, the user self-rating is meaningfully below the model estimate, "
			"indicating the model may be slightly lenient for this user."
		)
		ccot_text = (
			"For next-session interpretation, be more cautious with optimistic model outputs. "
			"Require stronger supporting evidence before concluding high focus."
		)
	else:
		alignment_text = "User and model are generally aligned."
		why_text = (
			"In the latest discrepancy analysis, user and model scores are close, "
			"so no strong bias direction is observed."
		)
		ccot_text = (
			"Keep standard interpretation behavior. Prioritize multi-sensor consistency and uncertainty notes "
			"instead of adding strong user-specific score bias corrections."
		)

	if score_gap is None:
		gap_text = "Gap size could not be assessed from the latest record."
	elif score_gap >= 1.5:
		gap_text = "The latest session shows a large model-user discrepancy."
	elif score_gap >= 0.7:
		gap_text = "The latest session shows a moderate model-user discrepancy."
	else:
		gap_text = "The latest session shows a small model-user discrepancy."

	return {
		"alignment_summary": alignment_text,
		"why": why_text,
		"discrepancy_strength": gap_text,
		"ccot_adjustment_recommendation": ccot_text,
	}


def build_profile_from_latest(user_id, row, snowflake_session=None, cortex_model=None):
	now = datetime.now(timezone.utc).isoformat()
	if not row:
		return {
			"generated_at": now,
			"user_id": user_id,
			"sample_size": 0,
			"profile_confidence": 0.0,
			"summary": "No discrepancy analysis found for this user.",
			"source": {},
			"calibration": {},
			"sensor_evaluation_guide": {},
		}

	model_score = _to_float(row.get("model_score"))
	user_score = _to_float(row.get("user_score"))
	score_gap = _to_float(row.get("score_gap"))

	diff = None
	if model_score is not None and user_score is not None:
		diff = user_score - model_score

	profile_confidence = 0.55
	if score_gap is not None:
		profile_confidence += max(0.0, 0.2 - min(score_gap, 2.0) * 0.1)
	profile_confidence = min(0.8, round(profile_confidence, 3))

	model_name = cortex_model or os.getenv("PERSONALIZATION_CORTEX_MODEL", "claude-3-5-sonnet")
	sensor_eval_source = "snowflake_cortex"
	raw_llm_response = None
	prompt_used = None

	if snowflake_session is None:
		sensor_guide = _default_sensor_guide("Snowflake session unavailable.")
		sensor_eval_source = "fallback_no_snowflake"
	else:
		try:
			sensor_guide, raw_llm_response, prompt_used = evaluate_sensors_with_cortex(
				session=snowflake_session,
				model=model_name,
				user_id=user_id,
				row=row,
				model_score=model_score,
				user_score=user_score,
				score_gap=score_gap,
			)
		except Exception as exc:
			sensor_guide = _default_sensor_guide(f"Snowflake Cortex error: {exc}")
			sensor_eval_source = "fallback_cortex_error"

	calibration = build_calibration_text(diff=diff, score_gap=score_gap)

	return {
		"generated_at": now,
		"user_id": user_id,
		"sample_size": 1,
		"profile_confidence": profile_confidence,
		"summary": "Personalization profile generated from the most recent discrepancy analysis for next-session CCoT interpretation.",
		"source": {
			"session_discrepancy_analysis_id": row.get("id"),
			"created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
			"model_name": row.get("model_name"),
			"session_id": row.get("session_id"),
			"device_id": row.get("device_id"),
		},
		"calibration": calibration,
		"sensor_evaluation_source": sensor_eval_source,
		"sensor_evaluation_model": model_name,
		# Debug info can be useful while tuning prompts.
		"sensor_evaluation_debug": {
			"raw_response": raw_llm_response,
			"prompt_used": prompt_used,
		},
		"sensor_evaluation_guide": sensor_guide,
	}


def upsert_profile_to_db(connection, user_id, payload):
	query = """
		INSERT INTO user_personalization_profiles (
			user_id,
			source_discrepancy_analysis_id,
			profile_confidence,
			profile_payload,
			updated_at
		) VALUES (%s, %s, %s, %s::jsonb, NOW())
		ON CONFLICT (user_id)
		DO UPDATE SET
			source_discrepancy_analysis_id = EXCLUDED.source_discrepancy_analysis_id,
			profile_confidence = EXCLUDED.profile_confidence,
			profile_payload = EXCLUDED.profile_payload,
			updated_at = NOW()
	"""

	source = payload.get("source", {})
	with connection.cursor() as cursor:
		cursor.execute(
			query,
			(
				user_id.lower(),
				source.get("session_discrepancy_analysis_id"),
				payload.get("profile_confidence"),
				json.dumps(payload, ensure_ascii=False),
			),
		)
	connection.commit()


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
		assert_profile_table_exists(pg_connection)
		row = fetch_latest_discrepancy_row(pg_connection, user_id)
		payload = build_profile_from_latest(
			user_id=user_id,
			row=row,
			snowflake_session=sf_session,
		)
		saved_file = save_profile(output_path, payload)

		if store_db:
			upsert_profile_to_db(pg_connection, user_id, payload)
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
