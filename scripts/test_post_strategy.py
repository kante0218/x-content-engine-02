"""Offline regression check: python3 scripts/test_post_strategy.py (never posts)."""
import contextlib
import io
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

with patch.dict(os.environ, {"GEMINI_API_KEY": "offline-gemini", "ANTHROPIC_API_KEY": "offline-anthropic"}):
    import polish_draft as polish
    import generate_draft as generate

# No X client is imported or initialized by this test.
with patch.dict("sys.modules", {"post_tweet": SimpleNamespace(post=Mock(side_effect=AssertionError("must not post")))}):
    import pipeline

IS_WAKANA = hasattr(generate, "_call")


def response(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


def main():
    assert polish.LLM_API_KEY == "offline-gemini"
    assert generate.LLM_API_KEY == "offline-gemini"
    assert polish.Anthropic.__module__ == "llm_gemini"
    assert [m[0] for m in polish.LENGTH_MODES] == [30, 35, 25, 10]
    with patch.object(polish.random, "choices", return_value=[polish.LENGTH_MODES[0]]) as choices:
        assert polish._pick_length_instruction()[0] == "ひとこと"
        assert choices.call_args.kwargs["weights"] == [30, 35, 25, 10]
    try:
        polish._pick_length_instruction("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid length must fail")

    client = SimpleNamespace(messages=Mock())
    for label, cap in polish.LENGTH_CAPS.items():
        client.messages.create.return_value = response("あ" * cap)
        with patch.object(polish, "Anthropic", return_value=client):
            assert len(polish.polish("元の文", length=label)) == cap
        client.messages.create.return_value = response("あ" * (cap + 1))
        with patch.object(polish, "Anthropic", return_value=client):
            try:
                polish.polish("元の文", length=label)
            except RuntimeError:
                pass
            else:
                raise AssertionError("length cap must reject oversized text")

    client.messages.create.return_value = response("ラーメンが好きです。")
    with patch.object(polish, "Anthropic", return_value=client):
        assert len(polish.polish("ラーメンが好きです。", comment_cta=True)) <= 35
    assert "今回はコメ欄" not in client.messages.create.call_args.kwargs["messages"][0]["content"]

    for label, cap in polish.LENGTH_CAPS.items():
        if IS_WAKANA:
            with patch.object(generate, "_call", side_effect=["あ" * (cap + 1), "あ" * cap]) as call, patch.object(generate, "_active_bank", return_value=[]), patch.object(generate, "pick_theme", return_value=("daily", "好み", "種")):
                assert len(generate.generate(length=label)[1]) == cap
                assert all(c.args[2] == label for c in call.call_args_list)
        else:
            client.messages.create.side_effect = [response("あ" * (cap + 1)), response("あ" * cap)]
            with patch.object(generate, "Anthropic", return_value=client):
                assert len(generate.generate("F", "日常", "好み", [], length=label)) == cap
            client.messages.create.side_effect = None

    for body in ("ラーメンが好きです。", "あ" * 60):
        with tempfile.TemporaryDirectory() as folder:
            pending = Path(folder) / "sample.md"
            pending.write_text(body)
            with patch.object(pipeline, "oldest_pending", return_value=pending), patch.object(pipeline, "append_log") as log, patch.object(pipeline, "polish", return_value=body) as rewrite, patch.object(pipeline, "generate_reply", side_effect=AssertionError("short posts must not thread")), patch.dict(os.environ, {"X_REPLY_THREAD_RATE": "1", "X_LIVE_POST": "true"}), patch("sys.argv", ["pipeline.py", "--dry-run"]), contextlib.redirect_stdout(io.StringIO()):
                assert pipeline.main() == 0
                assert rewrite.call_args.kwargs["comment_cta"] is False
                assert rewrite.call_args.kwargs["length"] == ("ひとこと" if len(body) <= 35 else "短文")
                assert log.call_args.args[1]["thread"] is False
                assert pending.exists()
    print("PASS: provider key isolation, 4 caps, retries, fixed mix, tiny preservation, no short threads, dry-run no send")


if __name__ == "__main__":
    main()
