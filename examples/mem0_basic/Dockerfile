# Build Revok from the repository root.
# docker-compose passes context: ../.. so all repo files are available.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY revok/ ./revok/

# Include falkordb-lite so the demo can switch backends at runtime via config.
RUN pip install --no-cache-dir ".[falkordb-lite]"

ENV PYTHONPATH=/app

VOLUME ["/data"]
EXPOSE 7771

CMD ["python", "-m", "revok", "--config", "/config/revok.yaml"]
