FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .

RUN useradd --create-home appuser
USER appuser

# 1 worker: su 512 MB di RAM ogni worker uvicorn costa ~80-120 MB. BackgroundTasks girano nel
# processo che ha servito la richiesta, quindi più worker non condividono lo stato in memoria
# (non è un problema qui: lo stato dei job vive nel DB).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]
