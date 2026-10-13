# Local Embeddings

The self-hosted embedding layer provides an `EmbeddingProvider` abstraction, one local
sentence-transformer-compatible implementation, model-tagged vectors on skill chunks, and
readiness reporting for the loaded model. No hosted embedding API is used or supported in this
mode. Lexical, vector, and hybrid retrieval are documented in
[retrieval.md](retrieval.md); the MCP gateway accesses them through the API.

## Components

| Module | Responsibility |
| --- | --- |
| `app/embeddings/base.py` | `EmbeddingProvider` protocol: `embed_text`, `embed_documents`, `load`, `health`, `close` |
| `app/embeddings/config.py` | `EmbeddingConfiguration`, the frozen contract built from settings |
| `app/embeddings/local.py` | `LocalEmbeddingProvider`, device resolution, batching, retries |
| `app/embeddings/types.py` | `EmbeddingVector`, `EmbeddingModelInfo`, `EmbeddingHealth`, shared limits |
| `app/services/embedding_service.py` | Active-model policy and provider composition |
| `app/models/embedding.py` | The persisted vector fields and their invariants |

Callers depend on the protocol, never on the backend. `build_embedding_service()` is the only
place that selects `LocalEmbeddingProvider`, so a different provider can be introduced later
without touching services, repositories, or routes.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `EMBEDDING_ENABLED` | `true` | When false no provider is built and readiness does not require one |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Model repository id or local directory |
| `EMBEDDING_DIMENSIONS` | `384` | Expected output dimensions, validated against the loaded model |
| `EMBEDDING_DEVICE` | `auto` | `auto`, `cpu`, `cuda`, or `mps` |
| `EMBEDDING_BATCH_SIZE` | `32` | Texts sent to the model per encode call |
| `EMBEDDING_MAX_BATCH_TEXTS` | `256` | Hard limit on texts per `embed_documents` call |
| `EMBEDDING_MAX_TEXT_CHARACTERS` | `20000` | Hard limit on one text |
| `EMBEDDING_NORMALIZE` | `true` | Produce unit vectors, which cosine similarity expects |
| `EMBEDDING_MAX_ATTEMPTS` | `3` | Encode attempts before reporting the model unavailable |
| `EMBEDDING_RETRY_BACKOFF_SECONDS` | `0.5` | Base delay for exponential retry backoff |
| `EMBEDDING_MODEL_CACHE_DIRECTORY` | unset | Where model files are stored; `/var/cache/embeddings` in containers |
| `EMBEDDING_ALLOW_DOWNLOADS` | `true` | Set to `false` for air-gapped hosts that already hold the model |

Configuration is validated twice: `Settings` rejects invalid environment values, and
`EmbeddingConfiguration` re-validates its own invariants so the provider is safe to construct
directly from a CLI or test. The same environment always produces an equal, frozen
configuration, so the metadata stored with vectors is deterministic.

Remote code execution from model repositories is disabled (`trust_remote_code=False`) and is not
configurable.

## Model loading

The model is loaded once per process, behind a lock, so concurrent first requests share a single
load. Loading and encoding both run in worker threads through `asyncio.to_thread`, so a CPU-bound
model never blocks the event loop. Encoding is serialized because one loaded model is a shared,
non-thread-safe resource.

Dimensions are discovered from the model rather than assumed: the provider reads whichever
dimension accessor the backend exposes and otherwise probes the model with one short text. If the
discovered value differs from `EMBEDDING_DIMENSIONS`, loading fails with
`EmbeddingDimensionError` instead of producing vectors that could never be searched together.

A failed load is not cached, so the next readiness probe retries it. Transient encode failures are
retried with exponential backoff up to `EMBEDDING_MAX_ATTEMPTS` before `EmbeddingUnavailableError`
is raised.

## Devices

`EMBEDDING_DEVICE=auto` prefers CUDA, then MPS, and falls back to CPU when no accelerator is
present, so the platform runs on a CPU-only host. An explicitly requested accelerator that is not
available fails with `EmbeddingConfigurationError` rather than silently running on CPU.

## Stored vectors

Every vector is stored with the model metadata needed to keep it comparable:

```json
{
  "embedding": [0.0123, -0.0456, "..."],
  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "embedding_dimensions": 384,
  "embedding_generated_at": "2026-01-01T12:00:00Z"
}
```

Three layers prevent incompatible vectors from being stored side by side:

1. The document model requires the four fields together and requires the vector length to equal
   `embedding_dimensions`.
2. `EmbeddingService.verify()` rejects any vector whose model or dimensions differ from the active
   configuration, so a vector from another model cannot reach a repository.
3. The `skill_chunks` MongoDB validator accepts only arrays of doubles within the supported bounds,
   with complete metadata.

`skill_chunks` carries a standard `(embedding_model, embedding_dimensions)` index so a model
migration can find the chunks that still need re-embedding. The Vector Search index over these
vectors is documented in [retrieval.md](retrieval.md); its dimensions come from the loaded model,
so it always matches the vectors described here.

## Skill chunk embedding

`SkillService` accepts an optional chunk embedder. When one is configured, every chunk of a new
skill version is embedded before the version is installed; if embedding fails, no version is
written at all. Re-importing unchanged content stays a no-op and does not re-embed.

```bash
python -m app.cli.import_skills                  # embed with the configured local model
python -m app.cli.import_skills --no-embeddings  # store chunks without vectors
```

The importer reports `Chunks embedded` alongside the existing statistics.

## Health

`GET /api/v1/health/ready` includes the embedding model:

```json
{
  "status": "ready",
  "mongodb": true,
  "database": true,
  "replica_set": true,
  "mongot": true,
  "embedding": {
    "enabled": true,
    "available": true,
    "model": "sentence-transformers/all-MiniLM-L6-v2",
    "dimensions": 384,
    "device": "cpu"
  }
}
```

Readiness fails with `503` when embeddings are enabled but the model cannot be loaded. When
`EMBEDDING_ENABLED=false`, no provider is built and embedding never blocks readiness.

Startup verifies the model but does not crash the process when it is unusable; the failure is
logged as `embedding_model_unavailable` with the model name and error type, and readiness reports
it. Successful loads log `embedding_model_loaded` with the model, dimensions, device, and load
duration. Log records never contain credentials or document content.

## Changing the embedding model

Vectors from different models must never be mixed. To migrate:

1. Re-embed every affected document with the new model.
2. Rebuild the vector index for the new dimensions, which startup reconciles automatically.
3. Verify retrieval quality.
4. Switch `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` together.
5. Remove the superseded vectors only after the migration succeeds.

Because the active configuration is enforced on write, a mismatched `EMBEDDING_DIMENSIONS` fails
at startup rather than corrupting a collection.

## Offline and air-gapped hosts

Model files are stored in `EMBEDDING_MODEL_CACHE_DIRECTORY`, which the Compose deployment maps to
the named `agent_context_embedding_models` volume. Populate the volume once with an outbound
connection, or copy the model directory in, then set `EMBEDDING_ALLOW_DOWNLOADS=false` and point
`EMBEDDING_MODEL` at the local path. The platform needs no hosted embedding service at any point.

## Verification

```bash
python -m pytest
RUN_EMBEDDING_MODEL_TESTS=1 python -m pytest tests/integration/test_local_embedding_model.py
```

The opt-in checks load the configured model on CPU, assert the discovered dimensions, assert
normalized unit vectors, and assert that `payment idempotency` is closer to
`How do we prevent duplicate payment requests?` than to unrelated text.
