FROM python:3.11-slim

WORKDIR /app

COPY ip_monitor.py ./
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

USER 1000
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 CMD python -c "import telegram"

CMD ["python", "ip_monitor.py"]