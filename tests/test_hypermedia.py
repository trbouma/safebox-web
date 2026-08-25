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


def test_theme_script_controls_presentation_only() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "theme.js"
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

    assert "safebox_theme=" in script
    assert "document.documentElement" in script


def test_check_pane_script_loads_only_a_server_supplied_html_fragment() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "check-pane.js"
    ).read_text(encoding="utf-8")

    forbidden = (
        "preventDefault(",
        "XMLHttpRequest",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "document.cookie",
    )
    for browser_application_api in forbidden:
        assert browser_application_api not in script

    assert 'document.addEventListener("toggle"' in script
    assert "fetch(panel.dataset.checkUrl" in script
    assert 'headers: { Accept: "text/html" }' in script
