FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
ENV CHAIT_DATA_DIR=/data CHAIT_PORT=3100
EXPOSE 3100
CMD ["python", "server.py"]
