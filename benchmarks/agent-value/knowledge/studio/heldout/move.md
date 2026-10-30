# Room transfer

Record: studio-move
Revision: 1
Status: active
Source: fictional project operations handbook

A room transfer retains the booking ID and seat count, validates capacity at its new interval, and emits a single moved event. Conflicts or unavailable target rooms leave the original booking in place. A move to the identical room/time is a no-op. Occupancy in unrelated rooms cannot block the transfer.
