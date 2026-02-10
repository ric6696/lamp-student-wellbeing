import os
from pathlib import Path
import psycopg2
from mock_generator import generate_mock_batch
from ingest_logic import ingest_batch
import uuid

def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


# Database connection settings (matches docker-compose)
repo_root = Path(__file__).resolve().parents[1]
load_env(repo_root / ".env")

DB_CONFIG = {
    "dbname": os.getenv("POSTGRES_DB", "sensing_db"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "dev_password"),
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5433")
}

def run_test():
    try:
        # 1. Connect to the DB
        conn = psycopg2.connect(**DB_CONFIG)
        print("Connected to PostgreSQL successfully.")

        # 2. Generate a fake device and mock data
        test_device_id = uuid.uuid4()
        
        # Ensure the device exists in the 'devices' table first (Foreign Key constraint)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO devices (device_id, user_id, model_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (str(test_device_id), str(uuid.uuid4()), "iPhone 15 Pro")
            )
        
        mock_data = generate_mock_batch(test_device_id)
        print(f"Generated mock batch for device: {test_device_id}")

        # 3. Run the ingestion logic
        ingest_batch(conn, mock_data)
        
        print("Test complete! Data is now in the database.")
        conn.close()

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_test()