FROM python:3.12-slim
WORKDIR /app

RUN useradd -r -s /bin/false chait && mkdir -p /data && chown chait:chait /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY templates/ templates/

ENV CHAIT_DATA_DIR=/data CHAIT_PORT=3100
EXPOSE 3100
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3100/health')" || exit 1

USER chait
CMD ["python", "server.py"]
