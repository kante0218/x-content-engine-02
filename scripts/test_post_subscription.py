"""Offline tests for subscription posting and the single-cycle local runner."""
import fcntl
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Default must ignore API credentials left in .env.
with patch.dict(os.environ, {"LLM_PROVIDER": "claude_subscription"}):
    import post_llm as L
    import polish_draft as P
    import generate_draft as G
import local_post as R


class PostSubscriptionTests(unittest.TestCase):
    def test_default_no_api_key_needed_in_generation_and_polish(self):
        self.assertIsNone(L.LLM_API_KEY)
        self.assertEqual(P.LLM_PROVIDER, "claude_subscription")
        with patch.object(L, "subscription_generate", return_value="ラーメンが好きです。") as generate:
            self.assertEqual(P.polish("ラーメンが好きです。", length="短文"), "ラーメンが好きです。")
            if hasattr(G, "_call"):
                self.assertEqual(G._call("日常", "ラーメンが好き", "短文"), "ラーメンが好きです。")
            else:
                self.assertEqual(G.generate("F", "日常", "ラーメンが好き", [], length="短文"), "ラーメンが好きです。")
            self.assertGreaterEqual(generate.call_count, 2)
            self.assertTrue(all(isinstance(c.args[0], str) and isinstance(c.args[1], str) for c in generate.call_args_list))

    def test_subscription_failure_never_falls_back(self):
        with patch.object(L, "subscription_generate", side_effect=RuntimeError("subscription limit")) as generate:
            with self.assertRaises(RuntimeError): P.polish("ラーメンが好きです。", length="短文")
            generate.assert_called_once()

    def test_reply_uses_same_subscription(self):
        with patch.object(L, "subscription_generate", return_value="普段から大切にしていることです。") as generate:
            result = P.generate_reply("本文です。", "補足の原稿です。")
            self.assertIsInstance(result, str)
            self.assertGreaterEqual(generate.call_count, 1)

    def test_wrapper_passes_dry_run_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(R, "ROOT", Path(temp)), \
             patch.object(sys, "argv", ["local_post.py", "--dry-run"]), \
             patch.object(R.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(R.main(), 0)
            self.assertEqual(run.call_args.args[0][-1], "--dry-run")
            self.assertEqual(run.call_count, 1 if hasattr(G, "_call") else 2)
        with tempfile.TemporaryDirectory() as temp, patch.object(R, "ROOT", Path(temp)), \
             patch.object(R.subprocess, "run", return_value=SimpleNamespace(returncode=7)) as run:
            self.assertEqual(R.main(), 7)
            run.assert_called_once()

    def test_existing_process_lock_prevents_second_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "logs").mkdir()
            with (root / "logs/local_post.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(R, "ROOT", root), patch.object(R.subprocess, "run") as run:
                    self.assertEqual(R.main(), 0)
                    run.assert_not_called()


if __name__ == "__main__": unittest.main()
