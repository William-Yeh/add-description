---
title: Designing Reliable Retry Logic for Distributed Systems
date: 2026-02-10
tags: [distributed-systems, reliability, backend]
---

Retry logic is one of the most misunderstood reliability patterns in distributed systems. Done naively, retries amplify failures rather than mask them — a thundering herd of retrying clients can collapse a service that was just momentarily slow.

## The Core Problem with Simple Retries

A fixed retry interval is dangerous under load. If a service goes down and 10,000 clients all retry every 500ms in lockstep, they create periodic spikes of traffic precisely when the service is struggling to recover. The solution is exponential backoff with jitter: each retry waits twice as long as the previous, plus a random offset to break synchronization.

## Exponential Backoff with Jitter

The formula is straightforward: `wait = min(cap, base * 2^attempt) + random(0, jitter)`. The cap prevents waits from growing indefinitely; the jitter ensures clients desynchronize over time. AWS recommends a base of 100ms, a cap of 20 seconds, and full jitter (random between 0 and the calculated wait).

## Idempotency Is a Prerequisite

Retry logic is only safe when operations are idempotent — repeating them produces the same result. For non-idempotent operations like payment processing, use idempotency keys: a client-generated UUID sent with every request that the server uses to deduplicate. If the server sees the same key twice, it returns the cached result rather than processing again.

## Circuit Breakers

Retries alone cannot handle prolonged outages. A circuit breaker tracks failure rate over a sliding window and opens the circuit (failing fast without retrying) once failures exceed a threshold. After a cooldown period, it enters half-open state, allowing a single probe request to check if the dependency has recovered.
