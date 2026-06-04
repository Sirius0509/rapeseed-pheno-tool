FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=7860
ENV YOLO_CONFIG_DIR=/app/.ultralytics
ENV MPLCONFIGDIR=/app/.matplotlib

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 \
  && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -r /app/requirements.txt

COPY backend/app.py /app/app.py
COPY backend/yolo11n.pt /app/yolo11n.pt
COPY backend/runs/manual-seed-test/train/seed_detector/weights/best.pt /app/runs/manual-seed-test/train/seed_detector/weights/best.pt

RUN mkdir -p /app/data /app/datasets /app/runs /app/.ultralytics /app/.matplotlib

EXPOSE 7860

CMD uvicorn app:app --host 0.0.0.0 --port ${PORT}
