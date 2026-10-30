# Group booking

Record: studio-capacity
Revision: 1
Status: active
Source: fictional project operations handbook

Each booking has one room, integer start/end minutes and a positive seat count. Intervals are half-open. At every instant, overlapping occupied seats including the proposed group must fit capacity. Counting every booking that touches any part of a long interval together overstates occupancy. A rejection does not create a booking. End must exceed start.
