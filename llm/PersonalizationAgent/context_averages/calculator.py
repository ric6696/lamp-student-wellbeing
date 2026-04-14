import json


MENTAL_CONTEXT_OPTIONS = (
    "Very Low",
    "Low",
    "Neutral",
    "High",
    "Very High",
)

ACTIVITY_CONTEXT_OPTIONS = (
    "Study",
    "Lecture",
    "Group Meeting",
    "Reading",
    "Writing / Report Work",
)

ENVIRONMENT_CONTEXT_OPTIONS = (
    "Library",
    "Classroom",
    "Cafe",
    "Home",
    "Outdoor",
)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_context_value(value, allowed_values):
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    # Try exact match first
    if normalized in allowed_values:
        return normalized
    # Try case-insensitive match
    normalized_lower = normalized.lower()
    for option in allowed_values:
        if option.lower() == normalized_lower:
            return option
    return None


def _format_average_score(values):
    if not values:
        return "-"
    return round(sum(values) / len(values), 3)


def build_context_average_concentration_scores(rows):
    mental_scores = {option: [] for option in MENTAL_CONTEXT_OPTIONS}
    activity_scores = {option: [] for option in ACTIVITY_CONTEXT_OPTIONS}
    environment_scores = {option: [] for option in ENVIRONMENT_CONTEXT_OPTIONS}

    for row in rows or []:
        user_score = _to_float(row.get("user_score"))
        if user_score is None:
            continue

        pre_session_questions = row.get("pre_session_questions") or {}
        if isinstance(pre_session_questions, str):
            try:
                pre_session_questions = json.loads(pre_session_questions)
            except Exception:
                pre_session_questions = {}
        if not isinstance(pre_session_questions, dict):
            continue

        mental_readiness = _normalize_context_value(
            pre_session_questions.get("mental_readiness"),
            MENTAL_CONTEXT_OPTIONS,
        )
        activity_context = _normalize_context_value(
            pre_session_questions.get("activity_context"),
            ACTIVITY_CONTEXT_OPTIONS,
        )
        environment_context = _normalize_context_value(
            pre_session_questions.get("environment_context"),
            ENVIRONMENT_CONTEXT_OPTIONS,
        )

        if mental_readiness:
            mental_scores[mental_readiness].append(user_score)
        if activity_context:
            activity_scores[activity_context].append(user_score)
        if environment_context:
            environment_scores[environment_context].append(user_score)

    return {
        "mental_readiness": {
            option: _format_average_score(scores) for option, scores in mental_scores.items()
        },
        "activity_context": {
            option: _format_average_score(scores) for option, scores in activity_scores.items()
        },
        "environment_context": {
            option: _format_average_score(scores) for option, scores in environment_scores.items()
        },
    }
