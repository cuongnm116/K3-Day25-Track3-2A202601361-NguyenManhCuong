from __future__ import annotations

import pytest

from reliability_lab.chaos import calculate_recovery_time_ms, run_scenario, run_simulation
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.config import LabConfig, ScenarioConfig
from reliability_lab.gateway import ReliabilityGateway


def _small_config() -> LabConfig:
    return LabConfig.model_validate(
        {
            "providers": [
                {
                    "name": "primary",
                    "fail_rate": 0.0,
                    "base_latency_ms": 1,
                    "cost_per_1k_tokens": 0.01,
                },
                {
                    "name": "backup",
                    "fail_rate": 0.0,
                    "base_latency_ms": 1,
                    "cost_per_1k_tokens": 0.005,
                },
            ],
            "circuit_breaker": {
                "failure_threshold": 2,
                "reset_timeout_seconds": 0.01,
                "success_threshold": 1,
            },
            "cache": {
                "enabled": True,
                "backend": "memory",
                "ttl_seconds": 60,
                "similarity_threshold": 0.9,
            },
            "load_test": {"requests": 6, "seed": 25},
            "scenarios": [
                {
                    "name": "all_healthy",
                    "provider_overrides": {"primary": 0.0, "backup": 0.0},
                }
            ],
        }
    )


def test_calculate_recovery_time_from_transition_log() -> None:
    breaker = CircuitBreaker("primary", failure_threshold=1, reset_timeout_seconds=1)
    breaker.transition_log = [
        {"from": "closed", "to": "open", "reason": "threshold", "ts": 10.0},
        {"from": "open", "to": "half_open", "reason": "timeout", "ts": 11.0},
        {"from": "half_open", "to": "closed", "reason": "probe", "ts": 12.5},
    ]
    gateway = ReliabilityGateway([], {"primary": breaker})

    assert calculate_recovery_time_ms(gateway) == pytest.approx(2500.0)


def test_run_scenario_counts_cache_hits_as_successes() -> None:
    config = _small_config()
    scenario = ScenarioConfig(
        name="all_healthy",
        provider_overrides={"primary": 0.0, "backup": 0.0},
    )

    metrics = run_scenario(config, ["repeatable query"], scenario)

    assert metrics.total_requests == 6
    assert metrics.successful_requests == 6
    assert metrics.failed_requests == 0
    assert metrics.cache_hits == 5
    assert metrics.estimated_cost_saved == pytest.approx(0.005)


def test_run_simulation_records_scenario_evidence() -> None:
    metrics = run_simulation(_small_config(), ["repeatable query"])

    assert metrics.scenarios == {"all_healthy": "pass"}
    assert metrics.scenario_metrics["all_healthy"]["total_requests"] == 6


def test_run_scenario_rejects_empty_query_set() -> None:
    with pytest.raises(ValueError, match="At least one query"):
        run_scenario(_small_config(), [], ScenarioConfig(name="empty"))
