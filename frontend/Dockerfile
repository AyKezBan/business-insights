FROM python:3.11.9-slim
WORKDIR /app
COPY server.py ./
COPY web ./web
USER 65534:65534
EXPOSE 8766
CMD ["python", "server.py", "--host", "0.0.0.0"]
