"""
Runs once on first container start. Exchanges a one-time enrollment code 
for a permanent API key. Saves config to /data/config.json.
"""
import json
import os
import sys
import time
from pathlib import Path
import httpx

CONFIG_PATH = Path("/data/config.json")
CENTRAL_URL = os.getenv("CENTRAL_URL", "").rstrip("/")
ENROLL_CODE = os.getenv("ENROLL_CODE", "")
PI_HOSTNAME = os.getenv("PI_HOSTNAME", os.uname().nodename)


def already_enrolled() -> bool:
    return CONFIG_PATH.exists() and CONFIG_PATH.stat().st_size > 0


def enroll() -> dict:
    if not CENTRAL_URL or not ENROLL_CODE:
        print("ERROR: CENTRAL_URL and ENROLL_CODE required for first enrollment")
        sys.exit(1)

    print(f"Enrolling with {CENTRAL_URL} using code {ENROLL_CODE}...")
    for attempt in range(10):
        try:
            r = httpx.post(
                f"{CENTRAL_URL}/api/enroll",
                json={"enrollment_code": ENROLL_CODE, "hostname": PI_HOSTNAME},
                timeout=15.0,
            )
            if r.status_code == 200:
                data = r.json()
                CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
                CONFIG_PATH.write_text(json.dumps(data, indent=2))
                print(f"Enrolled as {data['pi_identifier']} at store {data['store_code']}")
                return data
            elif r.status_code == 400:
                print(f"Code rejected: {r.text}")
                sys.exit(1)
            else:
                print(f"Attempt {attempt + 1}: HTTP {r.status_code}, retrying in 5s")
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {e}")
        time.sleep(5)

    print("Enrollment failed after 10 attempts.")
    sys.exit(1)


def write_runtime_env(config: dict):
    env_file = Path("/data/.runtime.env")
    env_file.write_text(
        f"STORE_ID={config['store_id']}\n"
        f"PI_ID={config['pi_id']}\n"
        f"API_KEY={config['api_key']}\n"
        f"CENTRAL_URL={CENTRAL_URL}\n"
    )


if __name__ == "__main__":
    if already_enrolled():
        config = json.loads(CONFIG_PATH.read_text())
        print(f"Already enrolled as {config.get('pi_identifier')}")
    else:
        config = enroll()
    write_runtime_env(config)