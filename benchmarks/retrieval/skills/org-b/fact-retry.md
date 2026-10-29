---
id: fact-retry
name: Heron retry policy
version: 1.0.0
description: Original fictional engineering benchmark decision
category: engineering
tags: [fictional, benchmark]
trust_level: trusted
---
 FOREIGN_RETRY Heron retries only temporary network faults. The delay is min(2 raised to attempt, 32) seconds, where the first retry has attempt zero. A validation refusal is terminal.
