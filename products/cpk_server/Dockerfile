FROM python:3.12-slim

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/products/cpk_server/src

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "control-plane-kit-core @ https://github.com/OpenJ92/control-plane-kit/archive/f9fb7cc041ac1c3daf562d941676f67ad28722ee.zip#subdirectory=control-plane-kit-core" \
    "control-plane-kit-operations @ https://github.com/OpenJ92/control-plane-kit/archive/f9fb7cc041ac1c3daf562d941676f67ad28722ee.zip#subdirectory=control-plane-kit-operations" \
    "control-plane-kit-interpreters[docker] @ https://github.com/OpenJ92/control-plane-kit-interpreters/archive/b1bff88e08189e67949dcf481c50cb9a68630e2a.zip"

COPY products/cpk_server/src ./products/cpk_server/src

RUN adduser --disabled-password --gecos "" --uid 10001 cpk \
    && chown -R cpk:cpk /app

USER cpk
EXPOSE 8080

CMD ["python", "-m", "control_plane_kit_servers_cpk_server.server"]
