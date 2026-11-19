from liveclips.control.discord_bot import build_panel_view, build_platforms_view
from liveclips.control.panel import PanelState


def state(**kw):
    base = dict(text="t", paused=False, min_score=6.0, interval=45, limit=8,
                platforms=[("instagram", True, True), ("youtube", True, False),
                           ("tiktok", False, True)])
    base.update(kw)
    return PanelState(**base)


def test_panel_view_has_controls_with_current_values():
    view = build_panel_view(state())
    ids = [c.custom_id for c in view.children]
    assert ids == ["panel:pause", "panel:score", "panel:pace"]
    score = view.children[1]
    assert next(o for o in score.options if o.default).value == "6"
    pace = view.children[2]
    assert len(pace.options) == 16 and next(o for o in pace.options if o.default).value == "45|8"


def test_platforms_view_marks_state_and_disables_unconfigured():
    view = build_platforms_view(state())
    by_id = {c.custom_id: c for c in view.children}
    assert by_id["panel:plat-instagram"].label == "Reels ✅"
    assert by_id["panel:plat-tiktok"].label == "TikTok ⚪"
    assert by_id["panel:plat-youtube"].disabled is True
