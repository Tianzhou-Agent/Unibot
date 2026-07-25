FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 sandbox

WORKDIR /opt/sandboxd
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

RUN mkdir -p /workspace /tmp/sandbox \
    && chown -R sandbox:sandbox /workspace /tmp/sandbox /opt/sandboxd

USER 1000:1000
ENV SANDBOX_WORKSPACE=/workspace
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
