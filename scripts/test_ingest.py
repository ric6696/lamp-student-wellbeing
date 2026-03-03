import os
from pathlib import Path
from mock_generator import generate_mock_batch
import sys
from pathlib import Path
repo_root = Path(__file__).resolve().parents[1]
# Load `.env` into this process environment before importing backend so pydantic picks it up
def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

load_env(repo_root / ".env")
sys.path.insert(0, str(repo_root))
from backend.app.ingest import ingest_batch as backend_ingest
from backend.app.models import Batch
import uuid

def run_test():
    try:
        # 1. Generate a fake device and mock data
        test_device_id = uuid.uuid4()
        mock_data = generate_mock_batch(test_device_id)
        print(f"Generated mock batch for device: {test_device_id}")

        # 2. Convert to backend Batch model and call backend ingestion
        batch = Batch.model_validate(mock_data)
        backend_ingest(batch)

        print("Test complete! Data is now in the database.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_test()