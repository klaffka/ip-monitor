FROM python:3.11-slim

WORKDIR /app

COPY ip_monitor.py ./
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "ip_monitor.py"]