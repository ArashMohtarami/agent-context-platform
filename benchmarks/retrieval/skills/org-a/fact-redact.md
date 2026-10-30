---
id: fact-redact
name: Wren safe logging
version: 1.0.0
description: Original fictional engineering benchmark decision
category: engineering
tags: [fictional, benchmark]
trust_level: trusted
---
Wren log sanitization removes keys named token, password and secret case insensitively at every nested dictionary depth and within lists. Other data and input objects remain unchanged.
