import json
import uuid
from datetime import datetime, timedelta
import random

def generate_mock_batch(device_id):
    now = datetime.utcnow()

    payload = [
        # GPS Point
        {
            "t": now.isoformat(),
            "type": "gps",
            "lat": 34.0522 + random.uniform(-0.01, 0.01),
            "lon": -118.2437 + random.uniform(-0.01, 0.01),
            "acc": 5.0
        },
        # Heart Rate
        {
            "t": (now - timedelta(seconds=5)).isoformat(),
            "type": "vital",
            "code": 1, # Heart Rate
            "val": random.randint(60, 100)
        },
        # Motion State
        {
            "t": (now - timedelta(seconds=10)).isoformat(),
            "type": "event",
            "label": "motion_state",
            "val_text": "walking"
        }
    ]

    return {
        "metadata": {
            "device_id": str(device_id),
            "version": "1.0"
        },
        "data": payload
    }

# Usage example for the team:
device_id = uuid.uuid4()
print(json.dumps(generate_mock_batch(device_id), indent=2))
