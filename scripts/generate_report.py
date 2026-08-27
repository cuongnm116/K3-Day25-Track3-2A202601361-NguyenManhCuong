from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from reliability_lab.config import load_config


def _read_json(path: str) -> dict[str, Any] | None:
    source = Path(path)
    if not source.exists():
        return None
    data: object = json.loads(source.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _number(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key, 0.0)
    return float(value) if isinstance(value, int | float) else 0.0


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _met(condition: bool) -> str:
    return "Yes" if condition else "No"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--no-cache-metrics", default="reports/metrics_no_cache.json")
    parser.add_argument("--redis-metrics", default="reports/metrics_redis.json")
    parser.add_argument("--redis-evidence", default="reports/redis_evidence.txt")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()

    metrics = _read_json(args.metrics)
    if metrics is None:
        raise FileNotFoundError(f"Metrics file not found or invalid: {args.metrics}")
    no_cache = _read_json(args.no_cache_metrics)
    redis_metrics = _read_json(args.redis_metrics)
    config = load_config(args.config)

    availability = _number(metrics, "availability")
    latency_p95 = _number(metrics, "latency_p95_ms")
    fallback_rate = _number(metrics, "fallback_success_rate")
    cache_hit_rate = _number(metrics, "cache_hit_rate")
    recovery_time = _number(metrics, "recovery_time_ms")

    lines = [
        "# Day 25 Reliability Engineering Final Report",
        "",
        "## 1. Architecture summary",
        "",
        (
            "The gateway checks the privacy-aware semantic cache first, then calls each provider "
            "through its own three-state circuit breaker. A failed or open primary is skipped in "
            "favor of the backup; only exhaustion of all providers returns the static degraded response."
        ),
        "",
        "```text",
        "User request",
        "    |",
        "    v",
        "[ReliabilityGateway] ---> [Memory or Redis semantic cache] -- HIT --> response",
        "    |                                      | MISS / guarded",
        "    v                                      v",
        "[Circuit breaker: primary] ------------> [Primary provider]",
        "    | OPEN / provider error",
        "    v",
        "[Circuit breaker: backup] -------------> [Backup provider]",
        "    | OPEN / provider error",
        "    v",
        "[Static degraded response]",
        "```",
        "",
        (
            "Each response exposes `route`, `provider`, `cache_hit`, latency, cost, and the final "
            "error when static fallback is used. Circuit transitions record source state, target "
            "state, reason, and wall-clock timestamp."
        ),
        "",
        "## 2. Configuration",
        "",
        "| Setting | Value | Reason |",
        "|---|---:|---|",
        f"| failure_threshold | {config.circuit_breaker.failure_threshold} | Opens quickly enough to stop repeated calls to a failing provider, while tolerating two isolated failures. |",
        f"| reset_timeout_seconds | {config.circuit_breaker.reset_timeout_seconds:g} s | Longer than one simulated provider call but below the 5 s recovery SLO; observed recovery is about {recovery_time:.0f} ms. |",
        f"| success_threshold | {config.circuit_breaker.success_threshold} | One successful probe restores service promptly in this sequential lab; production concurrency would normally require a guarded single probe. |",
        f"| cache backend | {config.cache.backend} | Memory is the deterministic baseline; Redis is measured separately to prove multi-instance sharing. |",
        f"| cache TTL | {config.cache.ttl_seconds} s | Covers a complete load run while bounding stale data; Redis `EXPIRE` enforces the same lifetime. |",
        f"| similarity_threshold | {config.cache.similarity_threshold:.2f} | A conservative threshold reduces semantic false hits; an explicit number/year guard rejects high-scoring 2024/2026 collisions. |",
        f"| requests per scenario | {config.load_test.requests} | Produces useful percentile and failure-rate samples without turning the local exercise into a long benchmark. |",
        f"| random seed | {config.load_test.seed} | Fixes query selection, jitter, failure, and token sequences for reproducible comparisons. |",
        "",
        "## 3. SLO definitions",
        "",
        "| SLI | SLO target | Actual value | Met? |",
        "|---|---|---:|---|",
        f"| Availability | >= 99% | {_percent(availability)} | {_met(availability >= 0.99)} |",
        f"| Latency P95 | < 2500 ms | {latency_p95:.2f} ms | {_met(latency_p95 < 2500)} |",
        f"| Fallback success rate | >= 95% | {_percent(fallback_rate)} | {_met(fallback_rate >= 0.95)} |",
        f"| Cache hit rate | >= 10% | {_percent(cache_hit_rate)} | {_met(cache_hit_rate >= 0.10)} |",
        f"| Recovery time | < 5000 ms | {recovery_time:.2f} ms | {_met(recovery_time < 5000)} |",
        "",
        "## 4. Metrics",
        "",
        f"Canonical evidence: `{args.metrics}` ({int(_number(metrics, 'total_requests'))} requests).",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]

    metric_keys = [
        "total_requests",
        "successful_requests",
        "failed_requests",
        "fallback_successes",
        "static_fallbacks",
        "cache_hits",
        "availability",
        "error_rate",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "fallback_success_rate",
        "cache_hit_rate",
        "estimated_cost",
        "estimated_cost_saved",
        "circuit_open_count",
        "recovery_time_ms",
    ]
    for key in metric_keys:
        if key in metrics:
            lines.append(f"| {key} | {metrics[key]} |")

    lines.extend(["", "## 5. Cache comparison", ""])
    if no_cache is None:
        lines.append(
            "No-cache evidence is not available. Run `python scripts/run_chaos.py "
            "--disable-cache --out reports/metrics_no_cache.json`."
        )
    else:

        def add_comparison(
            label: str,
            key: str,
            decimals: int = 2,
            suffix: str = "",
        ) -> None:
            before = _number(no_cache, key)
            after = _number(metrics, key)
            delta = after - before
            lines.append(
                f"| {label} | {before:.{decimals}f}{suffix} | "
                f"{after:.{decimals}f}{suffix} | {delta:+.{decimals}f}{suffix} |"
            )

        lines.extend(
            [
                "Both runs use the same seed and 300-request workload; only cache enablement changes.",
                "",
                "| Metric | Without cache | With cache | Delta |",
                "|---|---:|---:|---:|",
            ]
        )
        add_comparison("latency_p50_ms", "latency_p50_ms", suffix=" ms")
        add_comparison("latency_p95_ms", "latency_p95_ms", suffix=" ms")
        add_comparison("estimated_cost", "estimated_cost", decimals=6)
        add_comparison("cache_hit_rate", "cache_hit_rate", decimals=4)
        add_comparison("availability", "availability", decimals=4)
        add_comparison("circuit_open_count", "circuit_open_count", decimals=0)
        no_cache_cost = _number(no_cache, "estimated_cost")
        saved_fraction = (
            (no_cache_cost - _number(metrics, "estimated_cost")) / no_cache_cost
            if no_cache_cost
            else 0.0
        )
        lines.extend(
            [
                "",
                (
                    f"Cache reduced measured provider cost by {_percent(saved_fraction)} and avoided "
                    f"{int(_number(no_cache, 'circuit_open_count') - _number(metrics, 'circuit_open_count'))} "
                    "open transitions. Percentiles include provider calls only (`latency_ms > 0`), "
                    "so zero-latency cache hits improve cost and availability more visibly than "
                    "these latency percentiles."
                ),
            ]
        )

    lines.extend(
        [
            "",
            "## 6. Redis shared cache",
            "",
            (
                "An in-memory cache is private to one process, so separate gateway replicas repeat "
                "work and can serve inconsistent cache contents. `SharedRedisCache` stores the "
                "original query and response in a Redis hash with `EXPIRE`; exact lookups use a "
                "normalized MD5 key, while semantic lookups use `SCAN` plus the same similarity and "
                "safety guardrails."
            ),
            "",
            "### Evidence of shared state and guardrails",
            "",
            "```text",
        ]
    )
    evidence_path = Path(args.redis_evidence)
    if evidence_path.exists():
        lines.extend(evidence_path.read_text(encoding="utf-8").strip().splitlines())
    else:
        lines.append("Run: python scripts/verify_redis.py")
    lines.extend(["```", ""])

    if redis_metrics is not None:
        lines.extend(
            [
                "The Redis run started after flushing only the lab prefix and used the same seed.",
                "",
                "| Metric | In-memory cache | Redis cache | Delta (Redis - memory) |",
                "|---|---:|---:|---:|",
            ]
        )
        for label, key, decimals in [
            ("latency_p50_ms", "latency_p50_ms", 2),
            ("latency_p95_ms", "latency_p95_ms", 2),
            ("cache_hit_rate", "cache_hit_rate", 4),
            ("estimated_cost", "estimated_cost", 6),
        ]:
            memory_value = _number(metrics, key)
            redis_value = _number(redis_metrics, key)
            lines.append(
                f"| {label} | {memory_value:.{decimals}f} | {redis_value:.{decimals}f} | "
                f"{redis_value - memory_value:+.{decimals}f} |"
            )
        lines.extend(
            [
                "",
                (
                    "Redis showed a higher hit rate because entries are shared across gateway "
                    "instances created for successive scenarios. This is useful production behavior "
                    "but also why the lab prefix must be flushed before a cold-cache benchmark."
                ),
            ]
        )

    lines.extend(
        [
            "",
            "Equivalent Redis CLI inspection command:",
            "",
            "```powershell",
            'docker compose exec redis redis-cli --scan --pattern "rl:cache:*"',
            "```",
            "",
            "## 7. Chaos scenarios",
            "",
            "| Scenario | Expected behavior | Observed behavior | Status |",
            "|---|---|---|---|",
        ]
    )

    expectations = {
        "primary_timeout_100": "Primary fails 100%; breaker opens and backup serves traffic.",
        "primary_flaky_50": "Intermittent primary failures cause open/probe/recovery cycles.",
        "all_healthy": "Both providers succeed; traffic stays on primary except cache hits.",
    }
    statuses = metrics.get("scenarios", {})
    details = metrics.get("scenario_metrics", {})
    if isinstance(statuses, dict):
        for name, status in statuses.items():
            detail = details.get(name, {}) if isinstance(details, dict) else {}
            if isinstance(detail, dict) and detail:
                observed = (
                    f"{detail.get('successful_requests', '?')}/{detail.get('total_requests', '?')} "
                    f"successful; fallback={detail.get('fallback_successes', '?')}, "
                    f"static={detail.get('static_fallbacks', '?')}, "
                    f"cache hits={detail.get('cache_hits', '?')}, "
                    f"opens={detail.get('circuit_open_count', '?')}."
                )
            else:
                observed = "Scenario completed; see canonical metrics artifact."
            lines.append(
                f"| {name} | {expectations.get(str(name), 'Availability remains above 95%.')} "
                f"| {observed} | {str(status).upper()} |"
            )

    lines.extend(
        [
            "",
            (
                "Scenario pass criteria are explicit in `run_simulation`: timeout requires at least "
                "90% fallback success; flaky requires at least 95% availability and a circuit "
                "opening; healthy requires at least 99% availability and zero static fallbacks."
            ),
            "",
            "## 8. Failure analysis",
            "",
            (
                "The remaining production weakness is that circuit-breaker state is process-local. "
                "With three gateway replicas, each replica can independently send failures until its "
                "own threshold is reached, tripling load on an unhealthy provider and producing "
                "inconsistent recovery decisions. Before production, breaker counters and state "
                "should be coordinated through an atomic shared store (for example Redis "
                "transactions/Lua), with ownership of a single half-open probe, bounded operation "
                "timeouts, and a local fail-safe when the state store is unavailable."
            ),
            "",
            (
                "The semantic cache has a second known limit: character n-gram similarity is lexical, "
                "not true intent understanding. The numeric mismatch guard prevents the demonstrated "
                "dated-policy false hit, but broader evaluation and a versioned policy/tenant cache "
                "key are required."
            ),
            "",
            "## 9. Next steps",
            "",
            "1. Make the circuit breaker concurrency-safe and coordinate state plus a single half-open probe across replicas.",
            "2. Add Redis timeouts/circuit breaking with graceful degradation to a bounded local cache.",
            "3. Add tenant/policy-version cache keys, quality evaluation, and CI gates for SLO and false-hit regressions.",
            "",
            "### Reproduction commands (PowerShell)",
            "",
            "```powershell",
            "docker compose up -d",
            "python -m pytest -q",
            "python scripts/run_chaos.py --out reports/metrics.json",
            "python scripts/run_chaos.py --disable-cache --out reports/metrics_no_cache.json",
            "python scripts/run_chaos.py --cache-backend redis --flush-cache --out reports/metrics_redis.json",
            "python scripts/verify_redis.py",
            "python scripts/generate_report.py",
            "```",
        ]
    )

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
