#!/usr/bin/env python3
"""
Lightweight watcher that tails `logs/ingest_errors.log` and sends macOS
notifications for lines containing ERROR or CRITICAL.

Run in background alongside the existing `tail -F` monitor.
"""
import os
import time
import subprocess

LOG_PATH = os.path.join('logs', 'ingest_errors.log')
SLEEP = 0.5


def notify(message: str) -> None:
    safe = message.replace('"', '\\"')
    try:
        subprocess.run(['osascript', '-e', f'display notification "{safe}" with title "Ingest Error"'], check=False)
    except Exception:
        # best-effort: ignore notification failures
        pass


def follow(path: str):
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                time.sleep(SLEEP)
                continue
            yield line.rstrip('\n')


def ensure_log():
    if not os.path.exists(LOG_PATH):
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        open(LOG_PATH, 'a').close()


def main():
    ensure_log()
    for line in follow(LOG_PATH):
        if 'ERROR' in line or 'CRITICAL' in line:
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            out = f"{ts} {line}"
            print(out, flush=True)
            notify(line)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
