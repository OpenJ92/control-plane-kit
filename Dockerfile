# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim AS wheel

ENV PIP_NO_CACHE_DIR=1

WORKDIR /source

COPY pyproject.toml README.md LICENSE ./
COPY control_plane_kit ./control_plane_kit

RUN rm -rf build dist control_plane_kit.egg-info \
    && python -m pip wheel --no-deps --wheel-dir /wheelhouse .

FROM python:${PYTHON_VERSION}-slim AS base-wheel-test

COPY --from=wheel /wheelhouse /wheelhouse
COPY tests/packaging/base_wheel.py /acceptance/base_wheel.py

RUN WHEEL="$(find /wheelhouse -name '*.whl' -print -quit)" \
    && python -m pip install --no-cache-dir "$WHEEL" \
    && cd / \
    && python /acceptance/base_wheel.py

FROM python:${PYTHON_VERSION}-slim AS http-wheel-test

COPY --from=wheel /wheelhouse /wheelhouse
COPY tests/packaging/http_extra.py /acceptance/http_extra.py

RUN WHEEL="$(find /wheelhouse -name '*.whl' -print -quit)" \
    && python -m pip install --no-cache-dir "${WHEEL}[http]" \
    && cd / \
    && python /acceptance/http_extra.py

FROM python:${PYTHON_VERSION}-slim AS postgres-wheel-test

COPY --from=wheel /wheelhouse /wheelhouse
COPY tests/packaging/postgres_extra.py /acceptance/postgres_extra.py

RUN WHEEL="$(find /wheelhouse -name '*.whl' -print -quit)" \
    && python -m pip install --no-cache-dir "${WHEEL}[postgres]" \
    && cd / \
    && python /acceptance/postgres_extra.py

FROM python:${PYTHON_VERSION}-slim AS server-wheel-test

COPY --from=wheel /wheelhouse /wheelhouse
COPY tests/packaging/server_extra.py /acceptance/server_extra.py

RUN WHEEL="$(find /wheelhouse -name '*.whl' -print -quit)" \
    && python -m pip install --no-cache-dir "${WHEEL}[server]" \
    && cd / \
    && python /acceptance/server_extra.py

FROM python:${PYTHON_VERSION}-slim AS package

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY control_plane_kit ./control_plane_kit

RUN python -m pip install --upgrade pip \
    && python -m pip install ".[server]"

CMD ["python", "-c", "import control_plane_kit; print('control-plane-kit ready')"]

FROM package AS test

COPY examples ./examples
COPY tests ./tests

RUN python -m pip install ".[test-server]"

CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM docker:27-cli AS docker_cli

FROM test AS live-test

COPY --from=docker_cli /usr/local/bin/docker /usr/local/bin/docker

CMD ["python", "tests/live_docker_publication.py", "start"]

FROM package AS demo

COPY examples ./examples

RUN python -m pip install "psycopg[binary]>=3.2"

EXPOSE 8010

CMD ["uvicorn", "examples.read_interface_demo_server:create_app_from_environment", "--factory", "--host", "0.0.0.0", "--port", "8010"]
