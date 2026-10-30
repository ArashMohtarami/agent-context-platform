# Equipment loan deposits

Record: studio-equipment
Revision: 1
Status: active
Source: fictional project operations handbook

Loans require an active member and an available equipment item. Item deposits are fixed cents in the catalog. A loan transfers the item to checked_out, records the borrower, and increases held_deposit once. Repeating the same loan ID with the same member/item is harmless; reusing it differently is a conflict. A failed loan must change nothing.
