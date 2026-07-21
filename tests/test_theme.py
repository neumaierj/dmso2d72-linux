"""Theme definitions must stay legible in both schemes."""

from __future__ import annotations

import pytest

from dmso2d72.gui import theme as t


def test_both_themes_define_both_channels():
    for name in ("dark", "light"):
        colors = t.THEMES[name].channel_colors
        assert set(colors) == {1, 2}


@pytest.mark.parametrize("name", ["dark", "light"])
def test_curves_are_not_the_background(name):
    """Guards the regression where dark-theme yellow was drawn on white."""
    theme = t.THEMES[name]
    for color in theme.channel_colors.values():
        assert color.lower() != theme.plot_background.lower()
    assert theme.dmm_history.lower() != theme.plot_background.lower()


def test_light_and_dark_differ():
    dark, light = t.THEMES["dark"], t.THEMES["light"]
    assert dark.plot_background != light.plot_background
    # Curve colours must be per-theme, not just the background.
    assert dark.channel_colors != light.channel_colors


def test_resolve_named_themes():
    assert t.resolve("dark").name == "dark"
    assert t.resolve("light").name == "light"


def test_resolve_system_returns_a_real_theme(qapp):
    assert t.resolve("system", qapp).name in ("dark", "light")


def test_apply_to_app_returns_matching_theme(qapp):
    assert t.apply_to_app(qapp, "light").name == "light"
    assert t.apply_to_app(qapp, "dark").name == "dark"


def test_apply_to_app_falls_back_for_unknown_name(qapp):
    assert t.apply_to_app(qapp, "chartreuse").name in ("dark", "light")
