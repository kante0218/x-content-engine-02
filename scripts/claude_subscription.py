"""Generate text with the locally signed-in Claude subscription, without tools."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

CLAUDE = "/opt/homebrew/bin/claude"


def _environment() -> dict[str, str]:
    # Only the child environment changes; local OAuth/keychain login remains available.
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_", "BEDROCK_", "VERTEX_"))
            and key not in {"CLAUDECODE", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN",
                            "CLAUDE_CODE_API_KEY_HELPER_TTL_MS", "CLAUDE_CODE_BASE_URL"}}


def _run(arguments: list[str], cwd: str, env: dict[str, str], prompt: str = "", timeout: int = 120) -> dict:
    try:
        result = subprocess.run(arguments, input=prompt, text=True, encoding="utf-8",
                                capture_output=True, cwd=cwd, env=env, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Claudeサブスクの呼び出しに失敗しました。API課金へ切り替えず保留します") from exc
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Claude CLIの応答を確認できません。DM生成を保留します") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Claude CLIの応答形式が不正です")
    if result.returncode != 0:
        raise RuntimeError("Claude CLIの認証または生成が失敗しました。ログイン・利用上限を確認してください")
    return payload


def generate(prompt: str, system: str) -> str:
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(system, str) or not system.strip():
        raise RuntimeError("生成の入力または指示が空です")
    env = _environment()
    base = [CLAUDE, "--safe-mode", "--setting-sources", ""]
    with tempfile.TemporaryDirectory(prefix="dm-claude-") as neutral:
        status = _run(base + ["auth", "status", "--json"], neutral, env, timeout=20)
        if (status.get("loggedIn") is not True or status.get("authMethod") != "claude.ai"
                or status.get("apiProvider") != "firstParty"
                or str(status.get("subscriptionType", "")).lower() not in {"pro", "max", "team", "enterprise"}):
            raise RuntimeError("Claudeサブスクのログインを確認できません。claude auth login 後に再実行してください")
        payload = _run(base + ["-p", "--output-format", "json", "--no-session-persistence",
            "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--system-prompt", system], neutral, env, prompt)
    if (payload.get("type") != "result" or payload.get("subtype") != "success"
            or payload.get("is_error") is not False or not isinstance(payload.get("result"), str)
            or not payload["result"].strip()):
        raise RuntimeError("Claudeサブスクの生成が完了しませんでした。APIへ切り替えず保留します")
    return payload["result"].strip()
