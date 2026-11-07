FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    URNETWORK_CONFIG_FILE=/data/config.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 urnetwork \
    && useradd --uid 10001 --gid urnetwork --no-create-home --shell /usr/sbin/nologin urnetwork \
    && mkdir -p /data/state \
    && chown -R urnetwork:urnetwork /data \
    && chmod 700 /data /data/state

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

COPY main.py config.example.json ./
COPY core/ ./core/
COPY web/ ./web/
COPY docker/healthcheck.py ./docker/healthcheck.py

# Docker initializes a new named volume with this credential-free template.
RUN cp config.example.json /data/config.json \
    && chown urnetwork:urnetwork /data/config.json \
    && chmod 600 /data/config.json

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8777

# The existing server handles KeyboardInterrupt and closes managed gateways.
STOPSIGNAL SIGINT
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-m", "docker.healthcheck"]

ENTRYPOINT ["python", "/app/main.py"]
CMD ["--web", "--web-host", "0.0.0.0", "--web-port", "8777", "--state-dir", "/data/state"]
