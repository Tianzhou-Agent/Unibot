FROM python:3.12-slim

WORKDIR /operator
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py storage.py ./

USER 65532:65532
ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1
CMD ["kopf", "run", "--standalone", "/operator/main.py"]
