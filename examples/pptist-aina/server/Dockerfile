# The PPTist AINA's MCP tool server. Build context: this folder, plus the `unibot_aina` helper as the additional
# context of the same name (compose.yml points it at backend/src/unibot_aina in this repository).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8782

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=unibot_aina . ./unibot_aina
COPY main.py .

USER nobody
EXPOSE 8782
CMD ["python", "main.py"]
