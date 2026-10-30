FROM node:24.22.0-bookworm-slim@sha256:a5f4366e834695d5f363b920c104953a535d3a9864d1bdf74566a0ebe01daee5 AS console-builder
WORKDIR /build/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY dashboard/ ./
RUN npm run licenses && npm run build

FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8 AS console
COPY --from=console-builder /build/dashboard/dist/ /usr/share/nginx/html/console/
COPY --from=console-builder /build/dashboard/THIRD_PARTY_NOTICES.txt /usr/share/nginx/html/console/THIRD_PARTY_NOTICES.txt
COPY --from=console-builder /build/dashboard/licenses/ /usr/share/nginx/html/console/licenses/
COPY dashboard/LICENSE /usr/share/nginx/html/console/LICENSE
COPY deploy/console/ /etc/acp-console/
ENTRYPOINT ["/bin/sh", "/etc/acp-console/start.sh"]
EXPOSE 8443
HEALTHCHECK --interval=10s --timeout=5s --retries=6 CMD ["/bin/sh", "/etc/acp-console/healthcheck.sh"]

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

# Same Debian 13 release as the slim runtime; extract only Git/GSSAPI/CA runtime
# files and their notices, keeping compilers and package managers out of this layer.
FROM python:3.12.11@sha256:c1a5d356638cc86bd865d9019efbc34a6b0c3ad15a21e5ab4eb57bd2d1c3f7ce AS operator-tools
COPY docker/runtime/collect.py /collect.py
RUN python /collect.py

FROM python:3.12.11-slim@sha256:0b29ab9e420820f53d1cd5ce0157dfe07bea8a7cff5b4754d6d95c07b0e5bc47 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

# Git and GSSAPI runtime dependencies/notices come from a matching pinned Debian
# image. Package mirrors are not part of a clean build or a mutable dependency.
COPY --from=operator-tools /operator-runtime/ /

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
# Source/distribution tests inspect the maintained console/runbooks and build
# manifests. Dockerignore excludes private plans and generated browser state.
COPY dashboard ./dashboard
COPY docs ./docs
COPY website ./website
COPY deploy/console ./deploy/console
COPY deploy/keycloak ./deploy/keycloak
COPY .github ./.github
COPY examples ./examples
COPY scripts/codex-mcp-server.sh scripts/create-mongot-user.js \
     scripts/create-application-user.js scripts/verify-mongot.sh \
     scripts/backup.sh scripts/restore.sh scripts/prepare_open_source_release.py ./scripts/
COPY scripts/test-v6-deployment.sh scripts/test-v6-isolation.sh scripts/test-v6-acceptance.sh ./scripts/
# Focused/evaluation tests inspect the distributed commands and inputs; no runtime
# state, private evidence, CA or model cache is part of this test image.
COPY scripts/focused*.py scripts/test-focused.py scripts/evaluation_*.py scripts/evaluate.sh ./scripts/
COPY deploy/evaluation ./deploy/evaluation
COPY scripts/init-mongodb.sh scripts/init-replica-set.js scripts/mongodb-entrypoint.sh \
     scripts/mongodb-healthcheck.sh scripts/mongot-entrypoint.sh scripts/mongot-healthcheck.sh ./scripts/
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
