FROM python:3.12-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -s /bin/false chait && mkdir -p /data && chown chait:chait /data

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY server.py .
COPY templates/ templates/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV CHAIT_DATA_DIR=/data CHAIT_PORT=3100 CHAIT_HOST=0.0.0.0
EXPOSE 3100
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:3100/health')" || exit 1

# Start as root so the entrypoint can fix /data ownership on mounted volumes,
# then drop to the unprivileged 'chait' user (via gosu) to run the server.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "server.py"]
