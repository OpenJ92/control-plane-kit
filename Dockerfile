# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.14
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

RUN python -m pip install ".[test-server]" \
    && python -m unittest discover -s tests -v
