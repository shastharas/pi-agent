# Pi Agent — Network Monitoring

Lightweight ping worker + offline-resilient sync for multi-store network 
monitoring. Runs in Docker on Raspberry Pi 3 / 4 / 5 / Zero 2 W, or on any 
Linux host during development.

## What it does

- Pings configured network devices every 60 seconds (3 ICMP echoes each)
- Captures RTT min/avg/max and individual samples
- Stores all results locally in SQLite first (offline-resilient)
- Syncs to a central server when internet is available
- Auto-enrolls using a one-time code — no manual API key handling
- Pulls device config every 30 seconds — new devices added in the dashboard
  start being pinged within 30 seconds

## Quick start (Raspberry Pi or Docker host)

Get an enrollment code from your dashboard admin, then run:

```bash
curl -fsSL https://your-central-server.com/install.sh | sudo bash -s -- YOUR-ENROLLMENT-CODE
```

The installer will:
1. Install Docker if missing
2. Create `/opt/netmon-agent/` with docker-compose.yml
3. Pull and start the Pi agent container
4. Auto-enroll with your central server

## Manual setup

If you prefer manual setup:

1. Clone this repository:
```bash
git clone https://github.com/shastharas/pi-agent.git
cd pi-agent
```

2. Copy the example docker-compose file:
```bash
cp docker-compose.example.yml docker-compose.yml
```

3. Edit `docker-compose.yml` and set your environment variables:
```yaml
environment:
  CENTRAL_URL: https://your-central-server.com
  ENROLL_CODE: YOUR-ENROLLMENT-CODE
  PI_HOSTNAME: pi-store-001
```

4. Start the agent:
```bash
docker compose up -d
```

## Environment variables

- `CENTRAL_URL` — Your central monitoring server URL
- `ENROLL_CODE` — One-time enrollment code (only needed on first run)
- `PI_HOSTNAME` — Friendly name for this Pi (defaults to system hostname)
- `DB_PATH` — Local SQLite database path (default: `/data/local.db`)
- `SYNC_INTERVAL` — How often to sync with central server in seconds (default: 30)
- `CONFIG_SYNC_INTERVAL` — How often to pull device config in seconds (default: 30)

## Development

Run locally without Docker:

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export CENTRAL_URL=https://your-dev-server.com
export ENROLL_CODE=DEV-123456
export DB_PATH=./local.db

# Run enrollment (first time only)
python enroll.py

# Start workers
python ping_worker.py &
python sync_worker.py &
```

## Architecture

- **ping_worker.py** — Reads device list from local SQLite, pings each device, stores results
- **sync_worker.py** — Pushes ping results and logs to central server, pulls device config
- **enroll.py** — One-time enrollment with central server using enrollment code
- **entrypoint.sh** — Container startup script that handles enrollment and worker processes

## License

MIT License - see [LICENSE](LICENSE) file.