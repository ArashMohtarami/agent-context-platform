# Invoice reversal

Record: folio-reverse
Revision: 1
Status: active
Source: fictional project operations handbook

Reversals name an existing invoice and a unique reversal ID, with positive integer cents. The sum of reversals may not exceed the invoice amount. Record one negative ledger entry per accepted reversal; a repeated identical reversal is harmless and a different reuse conflicts. An invalid reversal changes neither reversals nor ledger.
