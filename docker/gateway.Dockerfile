FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY environments ./environments
EXPOSE 8080
ENTRYPOINT ["python", "-B", "-m", "tempera.observe.gateway"]
