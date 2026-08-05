from pathlib import Path


def test_form_script_is_progressive_enhancement_only() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "forms.js"
    ).read_text(encoding="utf-8")

    forbidden = (
        "preventDefault(",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "localStorage",
        "sessionStorage",
    )
    for browser_application_api in forbidden:
        assert browser_application_api not in script

    assert 'document.addEventListener("submit"' in script
    assert 'window.addEventListener("pageshow"' in script
