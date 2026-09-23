"""Local timing smoke checks and a real eight-second simulated AI deadline."""

import json
import os
import sys
from threading import Event
from time import monotonic
from unittest.mock import patch
from ai_recommender import AIRecommender
from engine import CareerEngine


def benchmark():
    engine = CareerEngine()
    with patch("ai_recommender.urlopen") as send:
        samples = []
        for _ in range(10):
            started = monotonic()
            engine.employee_view("E0028")
            samples.append(monotonic() - started)
        started = monotonic()
        engine.hr_view()
        hr_seconds = monotonic() - started
        offline_calls = send.call_count
    release = Event()
    worker_done = Event()

    def stalled(*args):
        release.wait(30)
        worker_done.set()
        return None

    try:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "benchmark-not-a-real-key", "OPENAI_MODEL": "mock", "CQ_ALLOW_EXTERNAL_AI": "1"}), patch("ai_recommender.request_ranking", side_effect=stalled):
            started = monotonic()
            fallback = AIRecommender().rerank(engine, "E0028")
            timeout_seconds = monotonic() - started
    finally:
        release.set()
        worker_done.wait(1)
    result = {"employee_profile_max_seconds": round(max(samples), 4), "hr_200_seconds": round(hr_seconds, 4),
              "ai_deadline_seconds": round(timeout_seconds, 4), "ai_fallback_reason": fallback["fallback_reason"],
              "offline_network_calls": offline_calls,
              "passed": max(samples) < 2 and hr_seconds < 2 and timeout_seconds < 10
                        and fallback["fallback_reason"] == "timeout" and offline_calls == 0}
    return result


if __name__ == "__main__":
    result = benchmark()
    print(json.dumps(result, indent=2))
    sys.exit(not result["passed"])
