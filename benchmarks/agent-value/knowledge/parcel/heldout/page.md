# Manifest directory

Record: parcel-page
Revision: 1
Status: active
Source: fictional project operations handbook

Manifest pages are ordered by (created_at, manifest_id), ascending. Cursors name the last returned pair and are exclusive. Every page belongs to one depot; records from other depots are not candidates. Page size is an integer from 1 through 50. Return a continuation cursor only if another matching row remains; browsing never changes manifests.
