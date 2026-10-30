# Station totals exchange

Record: harbor-units
Revision: 1
Status: active
Source: fictional project operations handbook

A totals batch belongs to one named station and day. Accept litres and millilitres only; normalize exactly to integer millilitres. A litre value is an integer, a millilitre value is an integer, and quantities are nonnegative. Any unknown unit, malformed member or unknown station rejects the entire batch. Sum once by external batch ID; conflicting reuse is refused.
