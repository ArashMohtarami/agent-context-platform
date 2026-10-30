# Period closing

Record: folio-close
Revision: 1
Status: active
Source: fictional project operations handbook

Closing a period freezes all its open invoices and appends one journal row with the sum of their amounts. The export receipt and frozen invoices are committed together. A failed export leaves all invoices open and adds neither receipt nor journal. Repeating a successful close returns the original journal identity without another row. Other periods are unchanged.
