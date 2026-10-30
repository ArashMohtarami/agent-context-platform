# Observation batch exchange

Record: harbor-import
Revision: 1
Status: active
Source: fictional project operations handbook

Import batches contain a station_id, batch_id and samples. Each sample has a unique integer timestamp and a nonnegative integer reading. Reject the complete batch if any member is malformed or duplicates an existing timestamp. Station existence is checked before import. A valid batch appends one audit event and keeps timestamp order; replaying an identical batch changes nothing.
