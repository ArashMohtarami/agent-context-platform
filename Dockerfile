FROM python:3.12.11-slim@sha256:0b29ab9e420820f53d1cd5ce0157dfe07bea8a7cff5b4754d6d95c07b0e5bc47 AS dependency-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /build
COPY requirements.lock ./
COPY .docker-wheels ./.docker-wheels

RUN python -m pip install --no-cache-dir --no-index --find-links=.docker-wheels \
    --target=/runtime -r requirements.lock

# Keep the supported MongoDB backup utilities at the same release source as
# the server. They are copied into the application image so `contextctl` and
# the tools coexist without publishing MongoDB or adding an operator image.
FROM mongo:8.3.9-noble@sha256:1cd50aca58bb5f84ca6bb32b69a75ad67b23867fc8454bd1ff288822e4599219 AS mongodb-tools

FROM python:3.12.11-slim@sha256:0b29ab9e420820f53d1cd5ce0157dfe07bea8a7cff5b4754d6d95c07b0e5bc47 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

# MongoDB Database Tools link GSSAPI even when password authentication is
# used. Keep only the runtime library; no Kerberos client configuration or
# service is introduced.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*

# Model weights live on a persistent volume owned by the runtime user, so they
# are downloaded once and never baked into the image.
ENV EMBEDDING_MODEL_CACHE_DIRECTORY=/var/cache/embeddings \
    HF_HOME=/var/cache/embeddings
RUN mkdir -p /var/cache/embeddings && chown app:app /var/cache/embeddings

COPY --from=dependency-builder /runtime /usr/local/lib/python3.12/site-packages
COPY --from=mongodb-tools /usr/bin/mongodump /usr/bin/mongorestore /usr/local/bin/
# Retain the upstream notices with the redistributed Database Tools binaries.
COPY deploy/licenses/mongodb-database-tools/ /usr/share/doc/mongodb-database-tools/
COPY app ./app
COPY skills ./skills

FROM runtime AS test

COPY .docker-wheels ./.docker-wheels
COPY requirements.lock requirements-dev.lock ./
RUN python -m pip install --no-cache-dir --no-index --find-links=.docker-wheels \
    -r requirements-dev.lock
COPY pyproject.toml docker-compose.yml Dockerfile ./
COPY .env.example .gitignore ./
COPY docker ./docker
# The tests assert about these, so the image has to contain them: the
# opt-in Codex configuration template, the launcher it names, the infrastructure
# verifier inspected by the Compose tests, the bootstrap script whose granted
# roles the security tests read, and the example project whose data is
# validated against the API's own schemas.
COPY examples/clients/codex-config.toml ./examples/clients/codex-config.toml
COPY deploy/systemd ./deploy/systemd
# Client-onboarding and security tests read committed configuration guidance,
# including the token-free Gemini and Claude examples.  Keep the original
# backup-contract copy explicit, then add only the client documents required
# by those tests; production images deliberately remain documentation-free.
COPY docs/backup.md ./docs/backup.md
COPY docs/codex.md docs/client-onboarding.md ./docs/
COPY docs/clients ./docs/clients
COPY examples ./examples
COPY scripts/codex-mcp-server.sh scripts/create-mongot-user.js \
     scripts/create-application-user.js scripts/verify-mongot.sh \
     scripts/backup.sh scripts/restore.sh scripts/prepare_open_source_release.py ./scripts/
COPY tests ./tests
RUN chown -R app:app /app

USER app
CMD ["python", "-m", "pytest"]

FROM runtime AS production

USER app
EXPOSE 8000
# So the image reports its own health, not only the Compose file that runs it.
# Readiness rather than liveness: an API that answers but cannot reach MongoDB
# is not healthy, and reporting it as such is how a rollout proceeds into an
# outage. The generous start period is the first load of the embedding model.
HEALTHCHECK --interval=10s --timeout=5s --start-period=180s --retries=6 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"]
CMD ["python", "-m", "app.run"]

# The MCP server speaks its protocol over stdin and stdout, so it binds no
# port and publishes none. A client spawns it and owns its lifetime; it
# reaches the platform through the Context API, never through MongoDB.
FROM runtime AS mcp

USER app
CMD ["python", "-m", "app.run_mcp"]
