from __future__ import annotations

import argparse

from redis import Redis

from reliability_lab.cache import SharedRedisCache


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Redis shared-cache behavior")
    parser.add_argument("--redis-url", default="redis://localhost:6379/0")
    parser.add_argument("--inspect-prefix", default="rl:cache:")
    args = parser.parse_args()

    evidence_prefix = "rl:evidence:"
    first = SharedRedisCache(args.redis_url, 60, 0.3, prefix=evidence_prefix)
    second = SharedRedisCache(args.redis_url, 60, 0.3, prefix=evidence_prefix)
    first.flush()

    try:
        first.set("shared query", "shared response")
        shared_value, shared_score = second.get("shared query")

        first.set("account balance for user 123", "private response")
        private_value, _ = second.get("account balance for user 123")

        first.set("refund policy for 2024", "old policy")
        false_hit_value, false_hit_score = second.get("refund policy for 2026")

        client: Redis[str] = Redis.from_url(args.redis_url, decode_responses=True)
        try:
            evidence_keys = sorted(client.scan_iter(f"{evidence_prefix}*"))
            lab_keys = sorted(client.scan_iter(f"{args.inspect_prefix}*"))
            shared_ttl = client.ttl(evidence_keys[0]) if evidence_keys else -2
        finally:
            client.close()

        print(f"redis_ping={first.ping()}")
        print(f"shared_value={shared_value!r}")
        print(f"shared_score={shared_score:.2f}")
        print(f"privacy_cached={private_value is not None}")
        print(f"false_hit_cached={false_hit_value is not None}")
        print(f"false_hit_score={false_hit_score:.4f}")
        print(f"false_hit_log_entries={len(second.false_hit_log)}")
        print(f"evidence_key_ttl_seconds={shared_ttl}")
        print(f"lab_cache_key_count={len(lab_keys)}")
        if lab_keys:
            print(f"sample_lab_cache_key={lab_keys[0]}")

        if shared_value != "shared response" or shared_score != 1.0:
            raise SystemExit("Shared-state verification failed")
        if private_value is not None or false_hit_value is not None:
            raise SystemExit("Redis cache guardrail verification failed")
    finally:
        first.flush()
        first.close()
        second.close()


if __name__ == "__main__":
    main()
