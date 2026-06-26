FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen --no-dev

COPY . .
 
# Hint ingress about the container's HTTP port. Railway uses this metadata
# for Dockerfile builds to route traffic correctly.
EXPOSE 8080

CMD uv run uvicorn main:app --host 0.0.0.0 --port $PORT
