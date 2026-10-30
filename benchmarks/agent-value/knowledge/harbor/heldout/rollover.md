# Mechanical counter consumption

Record: harbor-rollover
Revision: 1
Status: active
Source: fictional project operations handbook

Counters wrap at the configured modulus. Adjacent readings with increasing timestamps contribute their forward difference, adding the modulus when the number decreases. A reset marker on the later sample means the preceding gap is unknown and contributes no consumption. Report unknown gaps separately. Repeated or backwards timestamps and out-of-range counter readings are invalid.
