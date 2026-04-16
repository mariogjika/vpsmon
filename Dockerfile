FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    sshpass openssh-client smartmontools iproute2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/vpsmon
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt asyncssh

COPY vpsmon/ vpsmon/
COPY static/ static/

ENV VPSMON_HOST=0.0.0.0 \
    VPSMON_PORT=9090 \
    VPSMON_DATA_DIR=/data

VOLUME /data
EXPOSE 9090

CMD ["python", "-m", "vpsmon.app"]
