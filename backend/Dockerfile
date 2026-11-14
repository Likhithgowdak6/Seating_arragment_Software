FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements-ai.txt ./backend/
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu && pip install -r backend/requirements-ai.txt
COPY backend ./backend
RUN useradd --uid 10001 --create-home smartseat && mkdir -p /models /import && chown -R smartseat:smartseat /app /models /import
USER smartseat
EXPOSE 8000
CMD ["python", "-m", "backend.start"]
