"""Posting LLM selection. Local Claude subscription is the default; no fallback."""
import os
from types import SimpleNamespace

from claude_subscription import generate as subscription_generate

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "claude_subscription")
LLM_API_KEY = None

if LLM_PROVIDER == "claude_subscription":
    class Anthropic:
        def __init__(self, api_key=None):
            self.messages = self

        def create(self, *, system, messages, **kwargs):
            if len(messages) != 1 or messages[0].get("role") != "user" or not isinstance(messages[0].get("content"), str):
                raise ValueError("投稿生成は単一のテキスト入力だけを受け付けます")
            text = subscription_generate(messages[0]["content"], system)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])
elif LLM_PROVIDER == "gemini":
    from llm_gemini import Anthropic
    LLM_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY_2") or os.getenv("GOOGLE_API_KEY")
elif LLM_PROVIDER == "api":
    from anthropic import Anthropic
    LLM_API_KEY = os.getenv("ANTHROPIC_API_KEY")
else:
    raise RuntimeError("LLM_PROVIDERはclaude_subscription、gemini、apiのいずれかを指定してください")
