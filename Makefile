.PHONY: backup backups-verify check compose-config docker-wheels down format generate-secrets import-skills lint load-example reindex logs-mongot test test-embeddings test-integration test-integration-timings typecheck up verify-embeddings verify-mongodb verify-mongot

.PHONY: release release-check website

release-check:
	python scripts/prepare_open_source_release.py --check

release:
	python scripts/prepare_open_source_release.py

website:
	python website/build.py

generate-secrets:
	./scripts/generate-dev-secrets.sh

docker-wheels:
	./scripts/prepare-docker-wheels.sh

compose-config:
	docker compose config --quiet

up: generate-secrets docker-wheels
	docker compose up -d --build --wait

down:
	docker compose down

test:
	python -m pytest

test-integration:
	docker compose --profile test run --rm --build test

# Where the wall clock actually goes, when a live run is slower than expected.
test-integration-timings:
	docker compose --profile test run --rm --build test \
		python -m pytest -q --durations=25

test-embeddings:
	RUN_EMBEDDING_MODEL_TESTS=1 python -m pytest tests/integration/test_local_embedding_model.py

verify-embeddings:
	docker compose --profile test run --rm --build \
		-e RUN_EMBEDDING_MODEL_TESTS=1 test \
		python -m pytest tests/integration/test_local_embedding_model.py

load-example:
	python examples/payment-service/load.py

backup:
	docker compose exec -T context-api python -m app.cli.contextctl backup

backups-verify:
	docker compose exec -T context-api python -m app.cli.contextctl backups verify --restore

import-skills:
	docker compose run --rm --build context-api python -m app.cli.contextctl skills import

reindex:
	docker compose run --rm --build context-api python -m app.cli.contextctl skills reindex

lint:
	python -m ruff check .

format:
	python -m ruff format .

typecheck:
	python -m mypy app

check: test lint typecheck

verify-mongodb:
	docker compose run --rm --entrypoint /bin/bash mongodb-init /scripts/verify-mongodb.sh

verify-mongot:
	docker compose run --rm --entrypoint /bin/bash mongodb-init /scripts/verify-mongot.sh

logs-mongot:
	docker compose logs --follow mongot
