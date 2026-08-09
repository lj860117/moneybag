from pathlib import Path


CHAT_JS = Path(__file__).resolve().parents[1] / "pages" / "chat.js"


def test_stream_done_footer_uses_cached_done_model():
    text = CHAT_JS.read_text(encoding="utf-8")

    assert "_doneModel=''" in text
    assert "_formatModelName(_doneModel||d.model, _doneFallback||!!d.fallback_used)" in text
    assert "_formatModelName(d.model, d.fallback_used)" not in text
