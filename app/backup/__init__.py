"""Backups: what is worth keeping, for how long, and how to know it is sound.

MongoDB is the platform's only primary state. Everything else is derived —
search indexes are rebuilt from the documents, and the documents include the
vectors, so a restored database needs reindexing but never re-embedding.

Nothing here talks to MongoDB directly. The policy in this package is pure and
testable without a database; the service that drives `mongodump` and
`mongorestore` lives in `app.services.backup_service`.
"""
