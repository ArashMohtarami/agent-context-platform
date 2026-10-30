# Carrier timeline

Record: parcel-tracking
Revision: 1
Status: active
Source: fictional project operations handbook

Carrier events have a stable event_id and integer observed_at. Repeated identical event IDs are harmless; conflicting reuse is rejected. Timeline order is observed_at then event_id. The latest event determines status; receiving an older event must not rewind it. Each accepted new event is retained.
