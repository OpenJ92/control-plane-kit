FROM python:3.12-slim

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    PYTHONDONTWRITEBYTECODE=1 \
    CPK_SECRETS_DATABASE_PATH=/var/lib/cpk-secrets/secrets.sqlite3 \
    CPK_SECRETS_PROVIDER_ID=control-plane-kit

COPY products/secrets_server/entrypoint.sh /usr/local/bin/cpk-secrets-entrypoint

RUN python -m pip install --no-cache-dir \
    "control-plane-kit-secrets @ https://github.com/OpenJ92/control-plane-kit-secrets/archive/d1c923ca476a87f74af628b7e2fb9b7c0712debe.zip" \
    "uvicorn>=0.35" \
    && adduser --disabled-password --gecos "" --uid 10006 secrets \
    && chmod 0555 /usr/local/bin/cpk-secrets-entrypoint \
    && install -d -o secrets -g secrets -m 0700 \
        /run/secrets/cpk-secrets \
        /var/lib/cpk-secrets

USER secrets
EXPOSE 8081

ENTRYPOINT ["cpk-secrets-entrypoint"]
CMD ["uvicorn", "control_plane_kit_secrets.server:app", "--host", "0.0.0.0", "--port", "8081"]
