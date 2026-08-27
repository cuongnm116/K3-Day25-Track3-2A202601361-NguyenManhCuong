# Day 25 Reliability Engineering Final Report

## 1. Architecture summary

The gateway checks the privacy-aware semantic cache first, then calls each provider through its own three-state circuit breaker. A failed or open primary is skipped in favor of the backup; only exhaustion of all providers returns the static degraded response.

```text
User request
    |
    v
[ReliabilityGateway] ---> [Memory or Redis semantic cache] -- HIT --> response
    |                                      | MISS / guarded
    v                                      v
[Circuit breaker: primary] ------------> [Primary provider]
    | OPEN / provider error
    v
[Circuit breaker: backup] -------------> [Backup provider]
    | OPEN / provider error
    v
[Static degraded response]
```

Each response exposes `route`, `provider`, `cache_hit`, latency, cost, and the final error when static fallback is used. Circuit transitions record source state, target state, reason, and wall-clock timestamp.

## 2. Configuration

| Setting | Value | Reason |
|---|---:|---|
| failure_threshold | 3 | Opens quickly enough to stop repeated calls to a failing provider, while tolerating two isolated failures. |
| reset_timeout_seconds | 2 s | Longer than one simulated provider call but below the 5 s recovery SLO; observed recovery is about 2206 ms. |
| success_threshold | 1 | One successful probe restores service promptly in this sequential lab; production concurrency would normally require a guarded single probe. |
| cache backend | memory | Memory is the deterministic baseline; Redis is measured separately to prove multi-instance sharing. |
| cache TTL | 300 s | Covers a complete load run while bounding stale data; Redis `EXPIRE` enforces the same lifetime. |
| similarity_threshold | 0.92 | A conservative threshold reduces semantic false hits; an explicit number/year guard rejects high-scoring 2024/2026 collisions. |
| requests per scenario | 100 | Produces useful percentile and failure-rate samples without turning the local exercise into a long benchmark. |
| random seed | 25 | Fixes query selection, jitter, failure, and token sequences for reproducible comparisons. |

## 3. SLO definitions

| SLI | SLO target | Actual value | Met? |
|---|---|---:|---|
| Availability | >= 99% | 99.33% | Yes |
| Latency P95 | < 2500 ms | 317.42 ms | Yes |
| Fallback success rate | >= 95% | 96.97% | Yes |
| Cache hit rate | >= 10% | 58.67% | Yes |
| Recovery time | < 5000 ms | 2205.99 ms | Yes |

## 4. Metrics

Canonical evidence: `reports/metrics.json` (300 requests).

| Metric | Value |
|---|---:|
| total_requests | 300 |
| successful_requests | 298 |
| failed_requests | 2 |
| fallback_successes | 64 |
| static_fallbacks | 2 |
| cache_hits | 176 |
| availability | 0.9933 |
| error_rate | 0.0067 |
| latency_p50_ms | 261.98 |
| latency_p95_ms | 317.42 |
| latency_p99_ms | 319.6 |
| fallback_success_rate | 0.9697 |
| cache_hit_rate | 0.5867 |
| estimated_cost | 0.054128 |
| estimated_cost_saved | 0.176 |
| circuit_open_count | 8 |
| recovery_time_ms | 2205.99 |

## 5. Cache comparison

Both runs use the same seed and 300-request workload; only cache enablement changes.

| Metric | Without cache | With cache | Delta |
|---|---:|---:|---:|
| latency_p50_ms | 264.99 ms | 261.98 ms | -3.01 ms |
| latency_p95_ms | 316.93 ms | 317.42 ms | +0.49 ms |
| estimated_cost | 0.133010 | 0.054128 | -0.078882 |
| cache_hit_rate | 0.0000 | 0.5867 | +0.5867 |
| availability | 0.9800 | 0.9933 | +0.0133 |
| circuit_open_count | 20 | 8 | -12 |

Cache reduced measured provider cost by 59.31% and avoided 12 open transitions. Percentiles include provider calls only (`latency_ms > 0`), so zero-latency cache hits improve cost and availability more visibly than these latency percentiles.

## 6. Redis shared cache

An in-memory cache is private to one process, so separate gateway replicas repeat work and can serve inconsistent cache contents. `SharedRedisCache` stores the original query and response in a Redis hash with `EXPIRE`; exact lookups use a normalized MD5 key, while semantic lookups use `SCAN` plus the same similarity and safety guardrails.

### Evidence of shared state and guardrails

```text
redis_ping=True
shared_value='shared response'
shared_score=1.00
privacy_cached=False
false_hit_cached=False
false_hit_score=0.8824
false_hit_log_entries=1
evidence_key_ttl_seconds=60
lab_cache_key_count=13
sample_lab_cache_key=rl:cache:095946136fea
```

The Redis run started after flushing only the lab prefix and used the same seed.

| Metric | In-memory cache | Redis cache | Delta (Redis - memory) |
|---|---:|---:|---:|
| latency_p50_ms | 261.98 | 266.43 | +4.45 |
| latency_p95_ms | 317.42 | 316.28 | -1.14 |
| cache_hit_rate | 0.5867 | 0.6800 | +0.0933 |
| estimated_cost | 0.054128 | 0.040264 | -0.013864 |

Redis showed a higher hit rate because entries are shared across gateway instances created for successive scenarios. This is useful production behavior but also why the lab prefix must be flushed before a cold-cache benchmark.

Equivalent Redis CLI inspection command:

```powershell
docker compose exec redis redis-cli --scan --pattern "rl:cache:*"
```

## 7. Chaos scenarios

| Scenario | Expected behavior | Observed behavior | Status |
|---|---|---|---|
| primary_timeout_100 | Primary fails 100%; breaker opens and backup serves traffic. | 100/100 successful; fallback=44, static=0, cache hits=56, opens=6. | PASS |
| primary_flaky_50 | Intermittent primary failures cause open/probe/recovery cycles. | 98/100 successful; fallback=20, static=2, cache hits=60, opens=2. | PASS |
| all_healthy | Both providers succeed; traffic stays on primary except cache hits. | 100/100 successful; fallback=0, static=0, cache hits=60, opens=0. | PASS |

Scenario pass criteria are explicit in `run_simulation`: timeout requires at least 90% fallback success; flaky requires at least 95% availability and a circuit opening; healthy requires at least 99% availability and zero static fallbacks.

## 8. Failure analysis

The remaining production weakness is that circuit-breaker state is process-local. With three gateway replicas, each replica can independently send failures until its own threshold is reached, tripling load on an unhealthy provider and producing inconsistent recovery decisions. Before production, breaker counters and state should be coordinated through an atomic shared store (for example Redis transactions/Lua), with ownership of a single half-open probe, bounded operation timeouts, and a local fail-safe when the state store is unavailable.

The semantic cache has a second known limit: character n-gram similarity is lexical, not true intent understanding. The numeric mismatch guard prevents the demonstrated dated-policy false hit, but broader evaluation and a versioned policy/tenant cache key are required.

## 9. Next steps

1. Make the circuit breaker concurrency-safe and coordinate state plus a single half-open probe across replicas.
2. Add Redis timeouts/circuit breaking with graceful degradation to a bounded local cache.
3. Add tenant/policy-version cache keys, quality evaluation, and CI gates for SLO and false-hit regressions.

### Reproduction commands (PowerShell)

```powershell
docker compose up -d
python -m pytest -q
python scripts/run_chaos.py --out reports/metrics.json
python scripts/run_chaos.py --disable-cache --out reports/metrics_no_cache.json
python scripts/run_chaos.py --cache-backend redis --flush-cache --out reports/metrics_redis.json
python scripts/verify_redis.py
python scripts/generate_report.py
```
