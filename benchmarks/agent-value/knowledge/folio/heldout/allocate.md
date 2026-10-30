# Credit allocation

Record: folio-allocate
Revision: 1
Status: active
Source: fictional project operations handbook

Allocate a nonnegative integer credit across invoices in proportion to positive outstanding cents. First allocate whole-cent floors, then assign remaining cents by descending fractional remainder, invoice ID breaking ties. Never credit more than an invoice is owed. Reject a credit larger than the total. Zero credit changes nothing. Return invoice IDs in sorted order.
