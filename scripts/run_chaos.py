from __future__ import annotations

import argparse
from pathlib import Path

from reliability_lab.cache import SharedRedisCache
from reliability_lab.chaos import load_queries, run_simulation
from reliability_lab.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--out", default="reports/metrics.json")
    parser.add_argument("--csv", default=None, help="CSV output path (defaults beside JSON)")
    parser.add_argument(
        "--disable-cache",
        action="store_true",
        help="Disable cache for an A/B comparison run",
    )
    parser.add_argument(
        "--cache-backend",
        choices=("memory", "redis"),
        default=None,
        help="Override the cache backend from the config file",
    )
    parser.add_argument(
        "--flush-cache",
        action="store_true",
        help="Flush only this lab's Redis cache prefix before the run",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    if args.cache_backend is not None:
        config.cache.backend = args.cache_backend
    if args.disable_cache:
        config.cache.enabled = False
    if args.flush_cache:
        if not config.cache.enabled or config.cache.backend != "redis":
            parser.error("--flush-cache requires an enabled Redis cache")
        redis_cache = SharedRedisCache(
            config.cache.redis_url,
            config.cache.ttl_seconds,
            config.cache.similarity_threshold,
        )
        redis_cache.flush()
        redis_cache.close()
    metrics = run_simulation(config, load_queries())
    metrics.write_json(args.out)
    csv_path = args.csv or str(Path(args.out).with_suffix(".csv"))
    metrics.write_csv(csv_path)
    print(f"wrote {args.out}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
