ARG ULTRALYTICS_IMAGE=ultralytics/ultralytics:8.4.105
FROM ${ULTRALYTICS_IMAGE}

ARG MODEL_NAME=yolo26m.pt

LABEL org.opencontainers.image.title="Unibot YOLO Vision Service" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

WORKDIR /opt/models
RUN python -c "from ultralytics import YOLO; YOLO('${MODEL_NAME}')"

WORKDIR /srv/vision
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app.py LICENSE ./

ENV YOLO_DEVICE=auto \
    YOLO_IMAGE_SIZE=640 \
    YOLO_MAX_CONCURRENCY=1 \
    YOLO_MODEL_PATH=/opt/models/yolo26m.pt

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=12 \
  CMD python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3))['status'] == 'ready'"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
