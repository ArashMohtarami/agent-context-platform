# Evaluation inputs

Original MIT source; no generated configuration, secrets, certificates, backups,
database files or third-party assets belong here. `inputs.json` pins the existing
MongoDB/search images and names locally built reviewed application images. The
lifecycle reads the maintained base Compose as data and replaces every setting,
secret, mount, volume and network with fresh owned inputs; it never interpolates
deployment `.env` or starts the base Compose deployment.

Follow [the evaluation guide](../../docs/evaluation.md). Public source archives
include only these maintained files. Runtime state and evidence remain private.
