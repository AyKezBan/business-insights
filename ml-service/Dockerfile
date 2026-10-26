FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.lock .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# Фрагменты аналитики и новости монтируются томом в /app/data, трассы пишутся в /app/traces.
RUN useradd --create-home agent && mkdir -p data traces && chown agent data traces
USER agent

EXPOSE 8000
CMD ["uvicorn", "insight_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
