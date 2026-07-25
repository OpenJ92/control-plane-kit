FROM python:3.12-slim

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/products/cpk_server/src

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "control-plane-kit-core @ https://github.com/OpenJ92/control-plane-kit/archive/1bc014ae35571113424491f776c5b2ad0d2b41e6.zip#subdirectory=control-plane-kit-core" \
    "control-plane-kit-operations @ https://github.com/OpenJ92/control-plane-kit/archive/1bc014ae35571113424491f776c5b2ad0d2b41e6.zip#subdirectory=control-plane-kit-operations" \
    "control-plane-kit-interpreters[docker] @ https://github.com/OpenJ92/control-plane-kit-interpreters/archive/07e011286d9fe4801d536515c020624e3d7779ce.zip"

COPY products/cpk_server/src ./products/cpk_server/src

RUN adduser --disabled-password --gecos "" --uid 10001 cpk \
    && chown -R cpk:cpk /app

USER cpk
EXPOSE 8080

CMD ["python", "-m", "control_plane_kit_servers_cpk_server.server"]
