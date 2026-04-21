FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        iputils-ping \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ping_parser.py ping_worker.py sync_worker.py enroll.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

VOLUME ["/data"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./entrypoint.sh"]
