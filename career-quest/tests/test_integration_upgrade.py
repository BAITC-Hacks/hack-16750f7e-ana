from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ai_recommender import AIRecommender, snapshot, private_context, request_ranking, validate, validate_business
from ai_smoke_test import main as smoke
from engine import CareerEngine, parse_csv_text
from test_ai_recommender import answer
from test_import import kit
from upload_parser import _bundle_from_uploaded_files


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.engine = CareerEngine()

    def test_budget_filters_ai_and_cache_and_fallback(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "OPENAI_MODEL": "test", "CQ_ALLOW_EXTERNAL_AI": "1"}), patch("ai_recommender.request_ranking", side_effect=lambda context, *args: answer(context)) as call:
            ai = AIRecommender()
            full = ai.rerank(self.engine, "E0028", 8)
            limited = ai.rerank(self.engine, "E0028", 5)
            self.assertTrue(full["ai_used"] and limited["ai_used"])
            self.assertTrue(all(row["duration_hours"] <= 5 for row in limited["recommendations"]))
            self.assertEqual(call.call_count, 2)
            self.assertTrue(ai.rerank(self.engine, "E0028", 5)["cache_hit"])
            self.assertEqual(ai.rerank(self.engine, "E0028", 1)["recommendations"], [])

    def test_business_validation_independent_from_schema(self):
        _, candidates, context, _ = snapshot(self.engine, "E0028")
        valid = validate(answer(context), candidates)
        self.engine.complete("E0028", candidates[0]["event_id"])
        with self.assertRaises(ValueError):
            validate_business(valid, self.engine, "E0028", candidates)

    def test_identical_order_is_valid(self):
        _, _, context, _ = snapshot(self.engine, "E0028")
        valid = answer(context)
        valid["ordered_event_ids"].reverse()
        for index, key in enumerate(valid["ordered_event_ids"]):
            valid["reasons"][key]["why_over_next"] = "Приоритет по факторам." if index < len(valid["ordered_event_ids"]) - 1 else ""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "OPENAI_MODEL": "test", "CQ_ALLOW_EXTERNAL_AI": "1"}), patch("ai_recommender.request_ranking", return_value=valid):
            result = AIRecommender().rerank(self.engine, "E0028")
        self.assertTrue(result["ai_used"])
        self.assertEqual(result["recommendations"][0]["event_id"], self.engine.recommendations("E0028")[0]["event_id"])

    def test_wire_payload_excludes_private_text_in_all_catalog_fields(self):
        _, _, context, _ = snapshot(self.engine, "E0028")
        secret = "PRIVATE_PERSON private@example.org +77771234567"
        context["role"] = secret
        for row in context["skill_gaps"]:
            row["name"] = row["skill_id"] = secret
        for row in context["candidates"]:
            row["title"] = secret
            row["type"] = secret
            row["event_id"] += secret
            for skill in row["affected_skills"]:
                skill["name"] = skill["skill_id"] = secret
        response = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer(private_context(context)[0]))}]}]}
        with patch("ai_recommender.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            result = request_ranking(context, "test", "model")
        self.assertNotIn(secret, send.call_args.args[0].data.decode())
        self.assertIn(secret, result["ordered_event_ids"][0])  # mapped back locally

    def test_refusal_and_incomplete_do_not_pass(self):
        _, _, context, _ = snapshot(self.engine, "E0028")
        for response in ({"status": "incomplete"}, {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}):
            with patch("ai_recommender.urlopen", return_value=io.BytesIO(json.dumps(response).encode())), self.assertRaises(ValueError):
                request_ranking(context, "test", "model")

    def test_smoke_requires_explicit_gates(self):
        with patch.dict(os.environ, {}, clear=True), patch("ai_recommender.urlopen") as send, patch("builtins.print") as output:
            self.assertEqual(smoke(["--live"]), 0)
            self.assertIn("SKIPPED", output.call_args.args[0])
            send.assert_not_called()

    def test_preview_merge_and_replace(self):
        original = deepcopy(self.engine.history)
        revision = self.engine.revision
        bundle = {"employees": deepcopy(self.engine.employee("E0028")), "history": []}
        self.engine.upload_bundle(bundle, mode="merge", preview=True)
        self.assertEqual(self.engine.revision, revision)
        self.engine.upload_bundle(bundle, mode="merge")
        self.assertEqual(self.engine.history, original)
        self.assertIn("Демо", self.engine.data_source)
        self.engine.upload_bundle(kit(), mode="replace")
        self.assertEqual(len(self.engine.employees), 1)
        self.assertEqual(self.engine.employees[0]["employee_id"], "KIT")

    def test_strict_bom_csv_and_json(self):
        rows = parse_csv_text('\ufeffemployee_id;event_id;status;on_time\nKIT;E;skipped;false')
        self.assertEqual(rows[0]["on_time"], "false")
        for text in ('employee_id,event_id,status\n"KIT,E,skipped', 'employee_id,event_id,status\nKIT,E,skipped,extra'):
            with self.assertRaises(ValueError):
                parse_csv_text(text)
        for text in ('{"employees":[],"employees":[]}', '{"employees": NaN}'):
            with self.assertRaises(ValueError):
                _bundle_from_uploaded_files([{"name": "data.json", "content": text}])


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "state.sqlite")

    def test_restart_preserves_progress_and_idempotency(self):
        engine = CareerEngine(self.path)
        completed = engine.complete("E0028", "EV_SYSTEM_DESIGN")
        engine.set_participation("E0028", True)
        restored = CareerEngine(self.path)
        self.assertEqual(restored.employee_view("E0028")["gamification"], completed["gamification"])
        self.assertEqual(restored.employee_view("E0028")["readiness"], completed["readiness"])
        self.assertIn("E0028", restored.paused_employees)
        restored.set_participation("E0028", False)
        self.assertEqual(restored.complete("E0028", "EV_SYSTEM_DESIGN")["gamification"], completed["gamification"])

    def test_write_failure_rolls_back_complete_and_import(self):
        engine = CareerEngine(self.path)
        before = engine.employee_view("E0028")
        with patch.object(engine.store, "save", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                engine.complete("E0028", "EV_SYSTEM_DESIGN")
            with self.assertRaises(RuntimeError):
                engine.upload_bundle(kit(), mode="replace")
        self.assertEqual(engine.employee_view("E0028"), before)
        self.assertEqual(CareerEngine(self.path).employee_view("E0028"), before)

    def test_corrupt_file_kept(self):
        Path(self.path).write_bytes(b"corrupted")
        with self.assertRaises(RuntimeError):
            CareerEngine(self.path)
        self.assertEqual(Path(self.path).read_bytes(), b"corrupted")
