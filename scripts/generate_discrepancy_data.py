"""Generate sample discrepancy analysis data for 6 users (90 files each)."""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path


FACTOR_POOL = [
    "Environmental Noise / Speech",
    "Mental Readiness",
    "Session Duration",
    "Motion / Restlessness",
    "Location Stability",
    "Task-Environment Mismatch",
]

ACTIVITY_CONTEXTS = ["Reading", "Studying", "Problem Solving", "Writing", "Lecture Review"]
ENV_CONTEXTS = ["Cafe", "Library", "Dorm", "Classroom", "Home"]
READINESS_CONTEXTS = ["Very Low", "Low", "Medium", "High", "Very High"]

AGREEMENTS = ["MODEL_HIGHER", "USER_HIGHER", "ALIGNED"]
CONFIDENCE_CAPS = ["HIGH", "MEDIUM", "LOW"]
OBSERVABILITY = ["DIRECT", "PARTIAL", "INDIRECT"]
ALIGNMENTS = ["ALIGNED", "PARTIAL", "WEAK"]


def pick_factor_mappings(selected_factors: list[str]) -> list[dict]:
    mapping_templates = {
        "Environmental Noise / Speech": ["audio"],
        "Mental Readiness": ["vitals", "pre_session_questions"],
        "Session Duration": ["session_meta"],
        "Motion / Restlessness": ["motion", "vitals"],
        "Location Stability": ["gps", "motion"],
        "Task-Environment Mismatch": ["audio", "pre_session_questions"],
    }

    mappings = []
    for factor in selected_factors:
        mappings.append(
            {
                "factor": factor,
                "mapped_observability": random.choice(OBSERVABILITY),
                "mapped_primary_sensors": mapping_templates[factor],
                "confidence_cap": random.choice(CONFIDENCE_CAPS),
                "alignment_with_session_sensors": random.choice(ALIGNMENTS),
            }
        )
    return mappings


def build_prediction_judgment() -> dict:
    return {
        "audio": random.choice(
            [
                "Audio trend captured noise profile correctly, but distraction weighting should increase when speech overlaps sustained ambient noise.",
                "Audio captured environmental pattern; next run should penalize repeated speech bursts more aggressively for reading tasks.",
                "Audio signal quality was adequate. Improve policy by up-weighting persistent >60 dBA exposure in cognitively demanding sessions.",
            ]
        ),
        "vitals": random.choice(
            [
                "Vitals were partially informative; next run should treat missing HR windows as uncertainty rather than neutral evidence.",
                "Vitals alignment was acceptable, but confidence should be capped when biometric coverage is sparse.",
                "Physiological indicators should be used as supporting evidence, not a dominant signal, when environmental noise is primary.",
            ]
        ),
        "motion": random.choice(
            [
                "Motion settling was detected correctly; avoid over-crediting stillness when other distraction signals are strong.",
                "Motion context should stabilize interpretation, not override adverse audio conditions.",
                "Frequent posture changes should reduce focus confidence unless corroborated by improved audio and vitals.",
            ]
        ),
        "gps": random.choice(
            [
                "GPS is useful for confirming location stability; keep it as secondary context signal.",
                "GPS changes should trigger context shift checks, but not directly drive concentration score changes.",
                "Use GPS consistency to support motion interpretation while preserving audio-first weighting for environmental factors.",
            ]
        ),
    }


def generate_discrepancy_record(user_num: int, session_num: int) -> dict:
    model_score = random.randint(3, 10)
    user_score = random.randint(3, 10)
    gap = model_score - user_score

    if abs(gap) < 0.5:
        agreement = "ALIGNED"
    elif gap > 0:
        agreement = "MODEL_HIGHER"
    else:
        agreement = "USER_HIGHER"

    selected_count = random.randint(1, 3)
    selected_factors = random.sample(FACTOR_POOL, k=selected_count)

    summary = (
        f"Model score {model_score} vs user score {user_score} (gap {gap}). "
        f"Primary drivers: {', '.join(selected_factors)}. "
        f"Agreement classification: {agreement}."
    )

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": "claude-sonnet-4-5",
        "pre_session_questions": {
            "activity_context": random.choice(ACTIVITY_CONTEXTS),
            "environment_context": random.choice(ENV_CONTEXTS),
            "mental_readiness": random.choice(READINESS_CONTEXTS),
        },
        "result": {
            "overview": {
                "model_score": model_score,
                "user_score": user_score,
                "gap": gap,
                "agreement": agreement,
                "summary": summary,
            },
            "selected_factors": selected_factors,
            "factor_sensor_mappings": pick_factor_mappings(selected_factors),
            "why_difference": random.choice(
                [
                    "Model relied more heavily on objective sensor stability, while user-reported distraction tolerance was lower than inferred.",
                    "Short observation window likely caused the model to smooth over transient distractions that the user perceived strongly.",
                    "Policy anchored on historical averages, which may not reflect session-specific context and subjective readiness.",
                    "Compensatory weighting from motion stability likely offset negative environmental signals more than appropriate.",
                ]
            ),
            "prediction_agent_sensor_judgment": build_prediction_judgment(),
            "judgment_policy_next_run": [
                random.choice(
                    [
                        "Increase audio penalty when speech and moderate-to-high ambient noise co-occur.",
                        "Cap confidence when biometrics are missing or sparse during high-noise intervals.",
                        "Reduce compensatory credit from stationary motion when primary factors are adverse.",
                        "Apply stronger session-specific context weighting over historical baseline anchoring.",
                    ]
                ),
                random.choice(
                    [
                        "Use uncertainty-aware scoring for short sessions with incomplete multimodal coverage.",
                        "Preserve GPS as secondary evidence and prevent direct score inflation from location stability.",
                        "Add readiness-context interaction terms for activity and environment mismatch.",
                        "Separate 'attempted focus' from 'effective focus' in final score synthesis.",
                    ]
                ),
            ],
        },
    }

    return record


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1] / "llm" / "DiscrepancyAnalyst"
    seen_payloads: set[str] = set()

    for user_num in range(1, 7):
        user_dir = base_dir / f"user-{user_num}"
        user_dir.mkdir(parents=True, exist_ok=True)
        print(f"Generating discrepancy files for user-{user_num}...")

        for session_num in range(1, 91):
            while True:
                payload = generate_discrepancy_record(user_num, session_num)
                key = json.dumps(payload, sort_keys=True)
                if key not in seen_payloads:
                    seen_payloads.add(key)
                    break

            out_file = user_dir / f"session-{session_num:03d}_discrepancy.json"
            with out_file.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

        print(f"Completed user-{user_num}: 90 files")

    print("=" * 50)
    print("Discrepancy data generation complete")
    print("Generated 6 users x 90 files = 540 files")
    print(f"Unique payload count: {len(seen_payloads)}")
    print(f"Output location: {base_dir}")


if __name__ == "__main__":
    main()
