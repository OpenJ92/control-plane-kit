FROM python:3.12-slim

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/products/cpk_local_gateway/src \
    CPK_GATEWAY_TARGETS_JSON="{}"

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "control-plane-kit-core @ https://github.com/OpenJ92/control-plane-kit/archive/d2d81efb4d0f4ec7fd43145317779ed8e324833f.zip#subdirectory=control-plane-kit-core" \
    "fastapi>=0.115" \
    "PyJWT[crypto]>=2.10" \
    "uvicorn>=0.30" \
    "psycopg[binary]>=3.2"

COPY products/cpk_local_gateway/src ./products/cpk_local_gateway/src

RUN adduser --disabled-password --gecos "" --uid 10005 gateway \
    && chown -R gateway:gateway /app

USER gateway
EXPOSE 8000

CMD ["python", "-m", "control_plane_kit_servers_cpk_local_gateway.server"]
