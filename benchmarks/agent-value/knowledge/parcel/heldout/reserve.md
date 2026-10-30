# Reservation ledger

Record: parcel-reserve
Revision: 1
Status: active
Source: fictional project operations handbook

A reservation reduces on-hand stock only after all requested SKU quantities have been combined and validated. Quantities must be positive integers. A shortage or unknown SKU rejects the entire request, without inventory or reservation rows changing. Replaying the same reservation ID and normalized quantities is harmless; a different payload is a conflict.
