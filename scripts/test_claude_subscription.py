"""Offline subscription-only routing, isolation, and failure tests."""
import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import claude_subscription as S

STATUS = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": '{"personal":"test"}'}


def response(payload, code=0):
    return SimpleNamespace(returncode=code, stdout=json.dumps(payload), stderr="private error")


class SubscriptionTests(unittest.TestCase):
    def test_subscription_tools_disabled_and_billing_env_removed(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "not-for-this-call", "ANTHROPIC_AUTH_TOKEN": "x",
                "ANTHROPIC_BASE_URL": "https://proxy.invalid", "CLAUDE_CODE_USE_BEDROCK": "1"}), \
             patch.object(S.subprocess, "run", side_effect=[response(STATUS), response(RESULT)]) as run:
            self.assertEqual(S.generate('external `$(do not execute)` profile', 'trusted instructions'), RESULT["result"])
            self.assertEqual(len(run.call_args_list), 2)
            auth, generation = run.call_args_list
            for call in (auth, generation):
                cmd, kwargs = call.args[0], call.kwargs
                self.assertIn("--safe-mode", cmd)
                self.assertNotIn("--bare", cmd)
                self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")
                self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
                self.assertNotIn("ANTHROPIC_AUTH_TOKEN", kwargs["env"])
                self.assertNotIn("ANTHROPIC_BASE_URL", kwargs["env"])
                self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", kwargs["env"])
                self.assertNotIn("shell", kwargs)
                self.assertNotEqual(kwargs["cwd"], os.getcwd())
            cmd = generation.args[0]
            self.assertEqual(cmd[cmd.index("--tools") + 1], "")
            self.assertIn("--strict-mcp-config", cmd)
            self.assertIn("--no-session-persistence", cmd)
            self.assertEqual(generation.kwargs["input"], 'external `$(do not execute)` profile')
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "not-for-this-call")

    def test_non_subscription_auth_fails_before_generation(self):
        for status in ({"loggedIn": False}, {**STATUS, "authMethod": "api_key"},
                       {**STATUS, "subscriptionType": None}, {**STATUS, "apiProvider": "bedrock"}):
            with patch.object(S.subprocess, "run", return_value=response(status)) as run:
                with self.assertRaises(RuntimeError): S.generate("profile", "system")
                self.assertEqual(run.call_count, 1)

    def test_timeout_and_missing_cli_fail_without_retry(self):
        for error in (subprocess.TimeoutExpired("claude", 20), FileNotFoundError("missing")):
            with patch.object(S.subprocess, "run", side_effect=error) as run:
                with self.assertRaises(RuntimeError): S.generate("profile", "system")
                self.assertEqual(run.call_count, 1)

    def test_failed_empty_and_malformed_results_fail_closed(self):
        bad = [response({**RESULT, "is_error": True}), response({**RESULT, "subtype": "error_max_turns"}),
               response({**RESULT, "result": ""}), response(RESULT, code=1), response([]),
               SimpleNamespace(returncode=0, stdout="not json", stderr="private")]
        for result in bad:
            with patch.object(S.subprocess, "run", side_effect=[response(STATUS), result]) as run:
                with self.assertRaises(RuntimeError): S.generate("profile", "system")
                self.assertEqual(run.call_count, 2)

    def test_default_generation_uses_subscription_without_api_fallback(self):
        import dm_generate as G
        with patch.dict(os.environ, {}, clear=True), patch.object(S, "generate", side_effect=RuntimeError("limit")) as generate:
            with self.assertRaises(RuntimeError):
                if hasattr(G, "generate_one"):
                    G.generate_one(None, "", {"id": "42", "description": "Python engineer"})
                else:
                    G.generate("profile", "system")
            generate.assert_called_once()


if __name__ == "__main__": unittest.main()
