# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS management
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS server
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/ ./backend/
RUN pip install ./backend && \
    groupadd --gid 10001 rp && useradd --uid 10001 --gid rp --no-create-home rp && \
    mkdir -p /var/lib/rp /var/lib/rp-secrets && \
    chown -R rp:rp /var/lib/rp /var/lib/rp-secrets && chmod 700 /var/lib/rp /var/lib/rp-secrets
COPY --from=management /build/web/dist /app/static
ENV RP_DATA_DIR=/var/lib/rp RP_MASTER_KEY_FILE=/var/lib/rp-secrets/master.key RP_STATIC_DIR=/app/static
USER 10001:10001
EXPOSE 18765
STOPSIGNAL SIGTERM
ENTRYPOINT ["rp-server"]
CMD ["serve", "--host", "0.0.0.0", "--port", "18765"]
