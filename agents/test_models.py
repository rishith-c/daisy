import json
import os
import tempfile
import unittest

from agents.models import claude_models, codex_models, opencode_models


class ModelInventoryTests(unittest.TestCase):
    def test_codex_lists_every_visible_cached_model_with_its_real_ladders(self):
        with tempfile.TemporaryDirectory() as home:
            root = os.path.join(home, ".codex")
            os.makedirs(root)
            with open(os.path.join(root, "config.toml"), "w", encoding="utf-8") as fh:
                fh.write('model = "gpt-5.6-sol"\nmodel_reasoning_effort = "high"\n')
            cache = {"models": [
                {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol",
                 "visibility": "list", "supported_reasoning_levels": [
                     {"effort": "low"}, {"effort": "high"}],
                 "additional_speed_tiers": ["fast"]},
                {"slug": "gpt-5.6-terra", "display_name": "GPT-5.6-Terra",
                 "visibility": "list", "supported_reasoning_levels": [
                     {"effort": "medium"}, {"effort": "xhigh"}]},
                {"slug": "internal-review", "display_name": "Internal",
                 "visibility": "hidden", "supported_reasoning_levels": []},
            ]}
            with open(os.path.join(root, "models_cache.json"), "w", encoding="utf-8") as fh:
                json.dump(cache, fh)

            models = codex_models(home)

        self.assertEqual(["gpt-5.6-sol", "gpt-5.6-terra"], [m.id for m in models])
        self.assertTrue(models[0].current)
        self.assertEqual("high", models[0].effort)
        self.assertEqual(["low", "high"], models[0].efforts)
        self.assertEqual(["standard", "fast"], models[0].speeds)
        self.assertFalse(models[1].current)
        self.assertEqual(["medium", "xhigh"], models[1].efforts)

    def test_claude_lists_cached_entitlements_real_history_and_configured_alias(self):
        with tempfile.TemporaryDirectory() as home:
            root = os.path.join(home, ".claude")
            os.makedirs(root)
            with open(os.path.join(root, "settings.json"), "w", encoding="utf-8") as fh:
                json.dump({"model": "opus"}, fh)
            with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8") as fh:
                json.dump({"additionalModelOptionsCache": [
                    {"value": "claude-fable-5[1m]", "label": "Fable"}
                ]}, fh)
            history = os.path.join(root, "projects", "sample")
            os.makedirs(history)
            with open(os.path.join(history, "session.jsonl"), "w", encoding="utf-8") as fh:
                fh.write('{"message":{"model":"claude-sonnet-5"}}\n')
                fh.write('{"message":{"model":"<synthetic>"}}\n')

            models = claude_models(home)

        self.assertEqual(["claude-fable-5", "claude-sonnet-5", "opus"],
                         [m.id for m in models])
        self.assertEqual([False, False, True], [m.current for m in models])
        self.assertEqual(["automatic"], models[0].efforts)
        self.assertEqual(["standard"], models[0].speeds)
        self.assertNotIn("claude-haiku-5", [m.id for m in models])

    def test_opencode_uses_model_specific_variants_from_its_local_catalog(self):
        with tempfile.TemporaryDirectory() as root:
            db = os.path.join(root, "opencode.db")
            con = __import__("sqlite3").connect(db)
            con.execute("CREATE TABLE message (data TEXT)")
            con.execute("INSERT INTO message VALUES (?)", (json.dumps({
                "modelID": "ox-alpha", "providerID": "openrouter"
            }),))
            con.execute("INSERT INTO message VALUES (?)", (json.dumps({
                "modelID": "plain", "providerID": "local"
            }),))
            con.commit(); con.close()
            cache = os.path.join(root, "models.json")
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump({
                    "openrouter": {"models": {"ox-alpha": {
                        "name": "Ox Alpha", "reasoning_options": [
                            {"type": "effort", "values": ["low", "high", "max"]}
                        ]
                    }}},
                    "local": {"models": {"plain": {
                        "name": "Plain Local", "reasoning_options": []
                    }}},
                }, fh)

            models = opencode_models(db, cache)

        self.assertEqual(["Ox Alpha", "Plain Local"], [m.label for m in models])
        self.assertEqual(["low", "high", "max"], models[0].efforts)
        self.assertEqual(["automatic"], models[1].efforts)
        self.assertEqual([["standard"], ["standard"]], [m.speeds for m in models])


if __name__ == "__main__":
    unittest.main()
