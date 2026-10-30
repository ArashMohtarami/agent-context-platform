# Dispatch accounting

Record: parcel-dispatch
Revision: 1
Status: active
Source: fictional project operations handbook

Dispatch atomically consumes a ready reservation, creates one shipment, and appends one warehouse event. Any courier outage leaves the reservation ready and creates neither shipment nor event. Already dispatched reservations return their original shipment on repeat. Do not take stock again: reservation already consumed the units.
