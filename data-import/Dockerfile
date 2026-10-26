FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY data_core.py load_postgres.py ./
COPY sql ./sql
USER 65534:65534
ENTRYPOINT ["python", "load_postgres.py"]
CMD ["--data-dir", "/inputs"]
