"""Explicit live probe of the application's actual, bounded recommendation path."""
import argparse
import json
import os
from time import monotonic
from ai_recommender import AIRecommender, enabled
from engine import CareerEngine


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if not args.live or not enabled():
        missing = ([] if args.live else ["--live"])
        missing += [name for name in ("OPENAI_API_KEY", "OPENAI_MODEL") if not os.getenv(name, "").strip()]
        if os.getenv("CQ_ALLOW_EXTERNAL_AI") != "1":
            missing.append("CQ_ALLOW_EXTERNAL_AI=1")
        print("SKIPPED: не заданы " + ", ".join(missing) + ". Реальная AI-интеграция не проверена.")
        return 0
    # A fresh in-memory synthetic engine and empty cache. One call, no retries.
    engine = CareerEngine()
    started = monotonic()
    result = AIRecommender().rerank(engine, "E0028", hours=8)
    success = result["ai_used"] and result["decision_source"] == "hybrid_ai"
    print(json.dumps({"status": "PASS" if success else "FAIL", "elapsed_seconds": round(monotonic() - started, 3),
        **{key: result[key] for key in ("decision_source", "ai_used", "fallback_reason", "cache_hit")}}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
