from __future__ import annotations

import json
import random
from pathlib import Path

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.config import LabConfig, ScenarioConfig
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.metrics import RunMetrics
from reliability_lab.providers import FakeLLMProvider


def load_queries(path: str | Path = "data/sample_queries.jsonl") -> list[str]:
    queries: list[str] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        queries.append(json.loads(line)["query"])
    return queries


def build_gateway(
    config: LabConfig,
    provider_overrides: dict[str, float] | None = None,
    rng: random.Random | None = None,
) -> ReliabilityGateway:
    providers = []
    for p in config.providers:
        fail_rate = provider_overrides.get(p.name, p.fail_rate) if provider_overrides else p.fail_rate
        providers.append(
            FakeLLMProvider(
                p.name,
                fail_rate,
                p.base_latency_ms,
                p.cost_per_1k_tokens,
                rng=rng,
            )
        )
    breakers = {
        p.name: CircuitBreaker(
            name=p.name,
            failure_threshold=config.circuit_breaker.failure_threshold,
            reset_timeout_seconds=config.circuit_breaker.reset_timeout_seconds,
            success_threshold=config.circuit_breaker.success_threshold,
        )
        for p in config.providers
    }
    cache: ResponseCache | SharedRedisCache | None = None
    if config.cache.enabled:
        if config.cache.backend == "redis":
            cache = SharedRedisCache(
                config.cache.redis_url,
                config.cache.ttl_seconds,
                config.cache.similarity_threshold,
            )
        else:
            cache = ResponseCache(config.cache.ttl_seconds, config.cache.similarity_threshold)
    return ReliabilityGateway(providers, breakers, cache)


def calculate_recovery_time_ms(gateway: ReliabilityGateway) -> float | None:
    """Return average open-to-closed recovery time from breaker transition logs."""
    recovery_times: list[float] = []
    for breaker in gateway.breakers.values():
        opened_at: float | None = None
        for transition in breaker.transition_log:
            target_state = transition["to"]
            timestamp = float(transition["ts"])
            if target_state == "open":
                opened_at = timestamp
            elif target_state == "closed" and opened_at is not None:
                recovery_times.append((timestamp - opened_at) * 1000)
                opened_at = None

    if not recovery_times:
        return None
    return sum(recovery_times) / len(recovery_times)


def run_scenario(config: LabConfig, queries: list[str], scenario: ScenarioConfig) -> RunMetrics:
    """Run one deterministic chaos scenario and collect reliability metrics."""
    if not queries:
        raise ValueError("At least one query is required to run a chaos scenario")

    scenario_offset = sum(
        (index + 1) * ord(character) for index, character in enumerate(scenario.name)
    )
    scenario_seed = config.load_test.seed + scenario_offset
    query_rng = random.Random(scenario_seed)
    provider_rng = random.Random(scenario_seed + 1)
    gateway = build_gateway(
        config,
        scenario.provider_overrides or None,
        rng=provider_rng,
    )
    metrics = RunMetrics()

    for _ in range(config.load_test.requests):
        prompt = query_rng.choice(queries)
        result = gateway.complete(prompt)
        metrics.total_requests += 1
        metrics.estimated_cost += result.estimated_cost

        if result.cache_hit:
            metrics.cache_hits += 1
            metrics.estimated_cost_saved += 0.001

        if result.route == "fallback":
            metrics.fallback_successes += 1
            metrics.successful_requests += 1
        elif result.route == "static_fallback":
            metrics.static_fallbacks += 1
            metrics.failed_requests += 1
        else:
            metrics.successful_requests += 1

        if result.latency_ms > 0:
            metrics.latencies_ms.append(result.latency_ms)

    metrics.circuit_open_count = sum(
        1
        for breaker in gateway.breakers.values()
        for transition in breaker.transition_log
        if transition["to"] == "open"
    )
    metrics.recovery_time_ms = calculate_recovery_time_ms(gateway)

    if isinstance(gateway.cache, SharedRedisCache):
        gateway.cache.close()
    return metrics


def run_simulation(config: LabConfig, queries: list[str]) -> RunMetrics:
    """Run all named scenarios, apply criteria, and combine their metrics."""
    if not config.scenarios:
        default_scenario = ScenarioConfig(name="default", description="baseline run")
        metrics = run_scenario(config, queries, default_scenario)
        metrics.scenarios = {"default": "pass" if metrics.successful_requests > 0 else "fail"}
        metrics.scenario_metrics = {"default": _scenario_report(metrics)}
        return metrics

    combined = RunMetrics()
    recovery_times: list[float] = []
    for scenario in config.scenarios:
        result = run_scenario(config, queries, scenario)

        if scenario.name == "primary_timeout_100":
            passed = result.fallback_success_rate >= 0.9
        elif scenario.name == "primary_flaky_50":
            passed = result.availability >= 0.95 and result.circuit_open_count > 0
        elif scenario.name == "all_healthy":
            passed = result.availability >= 0.99 and result.static_fallbacks == 0
        else:
            passed = result.availability >= 0.95
        combined.scenarios[scenario.name] = "pass" if passed else "fail"
        combined.scenario_metrics[scenario.name] = _scenario_report(result)

        combined.total_requests += result.total_requests
        combined.successful_requests += result.successful_requests
        combined.failed_requests += result.failed_requests
        combined.fallback_successes += result.fallback_successes
        combined.static_fallbacks += result.static_fallbacks
        combined.cache_hits += result.cache_hits
        combined.circuit_open_count += result.circuit_open_count
        combined.estimated_cost += result.estimated_cost
        combined.estimated_cost_saved += result.estimated_cost_saved
        combined.latencies_ms.extend(result.latencies_ms)
        if result.recovery_time_ms is not None:
            recovery_times.append(result.recovery_time_ms)

    if recovery_times:
        combined.recovery_time_ms = sum(recovery_times) / len(recovery_times)

    return combined


def _scenario_report(metrics: RunMetrics) -> dict[str, object]:
    """Return compact, JSON-safe evidence for one chaos scenario."""
    report = metrics.to_report_dict()
    report.pop("scenarios", None)
    report.pop("scenario_metrics", None)
    return report
