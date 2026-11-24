# Radar de Passagens na internet. O Railway monta e liga este container sozinho.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RADAR_DADOS=/data
COPY requirements-servidor.txt .
RUN pip install --no-cache-dir -r requirements-servidor.txt
COPY robo ./robo
COPY config.yaml .
CMD gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 8 --timeout 120 robo.servidor:app
