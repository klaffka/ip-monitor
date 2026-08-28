FROM python:3.11-slim

WORKDIR /app

COPY ip_monitor.py ./
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/data && chown -R 1000:1000 /app/data
USER 1000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, sys, time; p = 'data/heartbeat'; sys.exit(0 if os.path.exists(p) and time.time() - os.path.getmtime(p) < 180 else 1)"

CMD ["python", "ip_monitor.py"]