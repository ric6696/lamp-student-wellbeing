#!/usr/bin/env python3
"""
Ingest synthesized FocusLLM dataset on Docker or EC2.
Loads synth_focusllm_user1.json, ingests by session, runs concentration for validation.
"""
import json
import os
import sys
import time
from pathlib import Path
from collections import defaultdict

repo_root = Path(__file__).resolve().parents[1]

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

from backend.app.db import close_pool, get_connection, init_pool, release_connection
from backend.app.ingest import ingest_batch
from backend.app.models import Batch
from backend.app.concentration import process_next_pending_job

def ingest_focusllm_dataset():
    """Load synth dataset, ingest by session, run concentration for validation."""
    dataset_path = repo_root / "llm" / "CCoT" / "output" / "synth_focusllm_user1.json"
    
    if not dataset_path.exists():
        print(f"Error: {dataset_path} not found. Run synthesize_focusllm_dataset.py first.")
        print(f"        Expected: {dataset_path}")
        sys.exit(1)
    
    print(f"Loading dataset from {dataset_path}...")
    with open(dataset_path, "r", encoding="utf-8") as f:
        full_payload = json.load(f)
    
    metadata = full_payload["metadata"]
    all_events = full_payload["data"]
    
    # Group events by session_key
    sessions_by_key = defaultdict(list)
    for event in all_events:
        session_key = event.get("metadata", {}).get("session_key") or "no-key"
        sessions_by_key[session_key].append(event)
    
    print(f"✓ Total events: {len(all_events)}")
    print(f"✓ Grouped into {len(sessions_by_key)} sessions")
    print(f"✓ User ID: {metadata['user_id']}")
    print(f"✓ Device ID: {metadata['device_id']}")
    
    init_pool()
    ingest_count = 0
    session_ids = []
    
    try:
        print("\n--- INGESTION PHASE ---")
        # Ingest all sessions
        for i, session_key in enumerate(sorted(sessions_by_key.keys()), 1):
            events = sessions_by_key[session_key]
            payload = {
                "metadata": metadata,
                "data": events
            }
            
            try:
                batch = Batch.model_validate(payload)
                ingest_batch(batch)
                ingest_count += 1
                if i % 10 == 0:
                    print(f"  [{i:2d}/{len(sessions_by_key)}] {session_key}: {len(events)} events ✓")
            except Exception as e:
                print(f"  ERROR on {session_key}: {e}")
        
        print(f"\n✓ Successfully ingested {ingest_count}/{len(sessions_by_key)} sessions")
        
        # Fetch last N session IDs for concentration validation
        print("\n--- VALIDATION PHASE ---")
        print("Fetching session IDs from database...")
        conn = None
        cur = None
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, user_id, started_at, ended_at FROM sessions "
                "WHERE user_id = %s ORDER BY id DESC LIMIT 15",
                (metadata["user_id"],),
            )
            rows = cur.fetchall()
            session_ids = [(row[0], row[1], row[2], row[3]) for row in rows]
            print(f"✓ Found {len(session_ids)} sessions in DB\n")
            for i, (sid, uid, start, end) in enumerate(session_ids[:5], 1):
                duration = (end - start).total_seconds() / 60
                print(f"  [{i}] session_id={sid}, user={uid}, duration={duration:.1f} min")
            if len(session_ids) > 5:
                print(f"  ... ({len(session_ids) - 5} more)")
        finally:
            if cur:
                cur.close()
            if conn:
                release_connection(conn)
        
        # Run concentration worker for up to 5 sessions to validate
        print("\n--- LLM CONCENTRATION PROCESSING ---")
        print("Running concentration worker for validation...")
        concentration_count = 0
        for i in range(min(5, len(session_ids))):
            worked = process_next_pending_job()
            if worked:
                concentration_count += 1
                print(f"  [{i+1}] Concentration job processed ✓")
                time.sleep(0.5)  # Brief delay between jobs
            else:
                print(f"  [{i+1}] No pending concentration job")
        
        print(f"\n✓ Processed {concentration_count} concentration jobs")
        
        # Fetch concentration results for validation
        print("\n--- RESULTS ---")
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT session_id, status, score, reason FROM session_concentration_analysis "
                "WHERE user_id = %s ORDER BY session_id DESC LIMIT 10",
                (metadata["user_id"],),
            )
            rows = cur.fetchall()
            if rows:
                print(f"✓ Found {len(rows)} concentration analyses:\n")
                for i, (sid, status, score, reason) in enumerate(rows[:5], 1):
                    reason_preview = (reason[:80] + "...") if reason and len(reason) > 80 else reason
                    status_ok = "✓" if status == "done" else "⚠"
                    print(f"  [{i}] {status_ok} session_id={sid}, status={status}, score={score}")
                    if reason_preview:
                        print(f"       reason: {reason_preview}")
                if len(rows) > 5:
                    print(f"\n  ... ({len(rows) - 5} more concentration analyses)")
            else:
                print("⚠ No concentration analyses found yet.")
        finally:
            if cur:
                cur.close()
            if conn:
                release_connection(conn)
        
        print("\n" + "="*60)
        print("✅ End-to-end test complete!")
        print("="*60)
        print(f"\nSummary:")
        print(f"  - Ingested:         {ingest_count} sessions")
        print(f"  - DB Sessions:      {len(session_ids)}")
        print(f"  - Concentration:    {concentration_count} analyzed")
        print(f"\nNext: Check concentration scores and reasoning at:")
        print(f"  llm/CCoT/output/concentration_analysis_results.json")
        
    finally:
        close_pool()

if __name__ == "__main__":
    ingest_focusllm_dataset()
