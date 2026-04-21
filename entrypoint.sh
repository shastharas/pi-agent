#!/bin/sh
set -e

if [ ! -f /data/config.json ]; then
    echo "First run. Starting enrollment..."
    python enroll.py
fi

if [ -f /data/.runtime.env ]; then
    set -a
    . /data/.runtime.env
    set +a
fi

echo "Starting workers..."
python ping_worker.py &
PING_PID=$!
python sync_worker.py &
SYNC_PID=$!

wait -n $PING_PID $SYNC_PID
EXIT_CODE=$?
echo "Worker exited with code $EXIT_CODE"
kill $PING_PID $SYNC_PID 2>/dev/null || true
exit $EXIT_CODE
