# PersonalizationAgent

Builds a per-user personalization profile from the latest row in `session_discrepancy_analyses`.

Main script:
- `llm/PersonalizationAgent/build_user_profile.py`

## What This Script Does

1. Reads the most recent discrepancy analysis for one user from PostgreSQL.
2. Builds text-based calibration guidance.
3. Generates sensor evaluation using Snowflake Cortex (semantic reasoning, no keyword matching).
4. Writes profile JSON to:
   - `llm/PersonalizationAgent/user_profile_summary.json`
5. Upserts profile payload into `user_personalization_profiles`.

## Important Design Rule

This script does **not** create DB schema.

Schema must be managed in migrations under `database/init`.

Required migration file:
- `database/init/04_user_personalization_profiles.sql`

If the table does not exist, the script raises an error and tells you to run that migration.

## Output Structure

`user_profile_summary.json` includes:
- `calibration` (text explanation, no numeric calibration fields)
- `sensor_evaluation_guide`
- `sensor_evaluation_source` (`snowflake_cortex` or fallback value)
- `sensor_evaluation_model`
- `sensor_evaluation_debug` (raw response and prompt, useful during tuning)

## Runtime Behavior

- Snowflake available:
  - Uses Cortex to generate sensor interpretation JSON.
- Snowflake unavailable or invalid LLM JSON:
  - Uses safe fallback sensor guide and still completes profile generation.

## Requirements

Python packages:
- `psycopg2-binary`
- `python-dotenv`
- `snowflake-snowpark-python`

PostgreSQL env vars:
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`

Snowflake env vars:
- `SNOWFLAKE_ACCOUNT`
- `SNOWFLAKE_USER`
- `SNOWFLAKE_USER_PASSWORD`
- `SNOWFLAKE_ROLE`
- `SNOWFLAKE_DATABASE`
- `SNOWFLAKE_SCHEMA`
- `SNOWFLAKE_WAREHOUSE`

Optional model override:
- `PERSONALIZATION_CORTEX_MODEL`

## How To Run

From repo root:

```bash
.venv/bin/python llm/PersonalizationAgent/build_user_profile.py
```

Current script defaults:
- `user_id="minsuk"`
- output file in same folder as script
- DB upsert enabled

If you want another user or custom output path, call `main(...)` directly from Python.

Example:

```python
from llm.PersonalizationAgent.build_user_profile import main

main(user_id="your_user_id", output_path="user_profile_summary.json", store_db=True)
```
