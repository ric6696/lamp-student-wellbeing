import json
import uuid
import random
from datetime import datetime, timedelta, timezone

# --- CONFIG ---
USER_ID = "user-832b2d98-bf98-4bd3-bca9-0f939391f0bb"  # Example user, can be looped for more
DEVICE_ID = str(uuid.uuid4())
START_DATE = datetime(2026, 2, 15, 8, 0, tzinfo=timezone.utc)
END_DATE = datetime(2026, 4, 15, 22, 0, tzinfo=timezone.utc)
N_SESSIONS = 100
SESSION_MIN = 60  # min session length in minutes
SESSION_MAX = 120  # max session length in minutes

# Personalization: user focuses best in library (quiet, low HR), worst in cafe (noisy, higher HR)
CONTEXTS = [
    {"name": "library", "prob": 0.25, "noise": (35, 45), "hr": (60, 75), "motion": "stationary", "focus_bias": 1},
    {"name": "cafe", "prob": 0.25, "noise": (55, 70), "hr": (80, 100), "motion": "walking", "focus_bias": -1},
    {"name": "home", "prob": 0.5, "noise": (40, 55), "hr": (65, 85), "motion": "stationary", "focus_bias": 0},
]

# About 15% of sessions should be strongly personalized (library sessions)

random.seed(42)
sessions = []
data = []
current = START_DATE
session_times = []

# Generate session start times
for _ in range(N_SESSIONS):
    # Random gap between sessions: 8-36 hours
    gap = timedelta(hours=random.uniform(8, 36))
    current += gap
    if current > END_DATE:
        break
    session_times.append(current)

for i, start_time in enumerate(session_times):
    # Pick context (weighted)
    context = random.choices(CONTEXTS, weights=[c["prob"] for c in CONTEXTS])[0]
    session_len = random.randint(SESSION_MIN, SESSION_MAX)
    end_time = start_time + timedelta(minutes=session_len)
    session_key = f"session-{i+1:03d}"

    # --- Session Markers ---
    data.append({
        "t": start_time.isoformat(),
        "type": "event",
        "label": "session_marker",
        "val_text": "START",
        "metadata": {"session_key": session_key, "context": context["name"]}
    })

    # --- Vitals (every 10 min) ---
    t = start_time
    while t < end_time:
        # Heart rate
        hr = random.uniform(*context["hr"])
        # Personalization: in library, HR is lower and less variable
        if context["name"] == "library":
            hr -= random.uniform(0, 5)
        data.append({
            "t": t.isoformat(),
            "type": "vital",
            "code": 1,
            "val": round(hr, 1),
            "metadata": {"session_key": session_key}
        })
        # Steps
        steps = random.randint(0, 10) if context["motion"] == "stationary" else random.randint(10, 50)
        data.append({
            "t": t.isoformat(),
            "type": "vital",
            "code": 20,
            "val": steps,
            "metadata": {"session_key": session_key}
        })
        # Distance
        dist = steps * random.uniform(0.6, 1.2)
        data.append({
            "t": t.isoformat(),
            "type": "vital",
            "code": 21,
            "val": round(dist, 2),
            "metadata": {"session_key": session_key}
        })
        t += timedelta(minutes=10)

    # --- GPS (start, mid, end) ---
    for frac in [0, 0.5, 1]:
        t_gps = start_time + timedelta(minutes=session_len * frac)
        lat = 22.3 + random.uniform(-0.01, 0.01)
        lon = 114.1 + random.uniform(-0.01, 0.01)
        data.append({
            "t": t_gps.isoformat(),
            "type": "gps",
            "lat": lat,
            "lon": lon,
            "acc": random.uniform(5, 15),
            "metadata": {"session_key": session_key, "context": context["name"]}
        })

    # --- Audio context (every 15 min) ---
    t = start_time
    while t < end_time:
        db = random.uniform(*context["noise"])
        conf = random.uniform(0.7, 1.0) if context["name"] != "cafe" else random.uniform(0.4, 0.9)
        ai_label = "Silence" if db < 45 else ("Speech" if db > 60 else "Ambient")
        data.append({
            "t": t.isoformat(),
            "type": "event",
            "label": "audio_context",
            "val_text": "quiet" if db < 45 else "busy",
            "metadata": {
                "db": f"{db:.1f}",
                "confidence": f"{conf:.2f}",
                "ai_label": ai_label,
                "ai_confidence": f"{conf:.2f}",
                "session_key": session_key,
                "context": context["name"]
            }
        })
        t += timedelta(minutes=15)

    # --- Motion context (every 20 min) ---
    t = start_time
    while t < end_time:
        val_text = context["motion"] if random.random() > 0.2 else ("walking" if context["motion"] == "stationary" else "stationary")
        data.append({
            "t": t.isoformat(),
            "type": "event",
            "label": "motion_context",
            "val_text": val_text,
            "metadata": {"session_key": session_key, "context": context["name"]}
        })
        t += timedelta(minutes=20)

    # --- Session END marker ---
    data.append({
        "t": end_time.isoformat(),
        "type": "event",
        "label": "session_marker",
        "val_text": "END",
        "metadata": {"session_key": session_key, "context": context["name"]}
    })

# --- Output as ingestible batch ---
out = {
    "metadata": {
        "user_id": USER_ID,
        "device_id": DEVICE_ID,
        "version": "1.0",
        "model_name": "simulator"
    },
    "data": data
}

with open("llm/CCoT/output/synth_focusllm_user1.json", "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

print(f"Generated {len(session_times)} sessions, {len(data)} events for user {USER_ID}.")
print("Output: llm/CCoT/output/synth_focusllm_user1.json")
