"""Gemini compatibility regression checks; no network calls."""
import json
import os
import unittest
from unittest.mock import patch

from llm_gemini import _Messages, _model_chain


class GeminiTests(unittest.TestCase):
    def test_current_default_models_and_explicit_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_model_chain(), ["gemini-flash-latest", "gemini-flash-lite-latest"])
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-flash-lite-latest"}, clear=True):
            self.assertEqual(_model_chain(), ["gemini-flash-lite-latest", "gemini-flash-latest"])

    def test_request_uses_model_default_thinking(self):
        client = _Messages(["test-key"])
        with patch.object(client, "_try_model", return_value=("本文", None, "")) as request:
            response = client.create(max_tokens=3000, system="system", messages=[{"content": "hello"}])
        body = json.loads(request.call_args.args[2])
        self.assertNotIn("thinkingConfig", body["generationConfig"])
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 3000)
        self.assertEqual(body["contents"][0]["parts"], [{"text": "hello"}])
        self.assertEqual(body["systemInstruction"]["parts"], [{"text": "system"}])
        self.assertEqual(response.content[0].text, "本文")

    def test_parser_excludes_thoughts(self):
        payload = {"candidates": [{"content": {"parts": [
            {"thought": True, "text": "internal reasoning"},
            {"text": "公開"}, {"thought": False, "text": "本文"},
            {"inlineData": {"mimeType": "image/png"}},
        ]}}]}
        self.assertEqual(_Messages._parse(payload), "公開本文")
        self.assertEqual(_Messages._parse({"candidates": [{"content": {"parts": [
            {"thought": True, "text": "internal only"}]}}]}), "")
        self.assertEqual(_Messages._parse({}), "")


if __name__ == "__main__":
    unittest.main()
