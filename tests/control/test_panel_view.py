from liveclips.control.discord_bot import build_panel_view, build_platforms_view
from liveclips.control.panel import PanelState


def state(**kw):
    base = dict(text="t", auto_on=True, paused=False, known=["Just Chatting", "GTA V"],
                selected=["GTA V"], min_viewers=1000, min_score=6.0, interval=45, limit=8,
                platforms=[("instagram", True, True), ("youtube", True, False),
                           ("tiktok", False, True)])
    base.update(kw)
    return PanelState(**base)


def test_panel_view_has_controls_with_current_values():
    view = build_panel_view(state())
    ids = [c.custom_id for c in view.children]
    assert ids == ["panel:auto", "panel:pause", "panel:addcat", "panel:cats",
                   "panel:viewers", "panel:score", "panel:pace"]
    cats = view.children[3]
    assert [o.label for o in cats.options] == ["Just Chatting", "GTA V"]
    assert [o.default for o in cats.options] == [False, True]
    viewers = view.children[4]
    assert next(o for o in viewers.options if o.default).value == "1000"
    pace = view.children[6]
    assert len(pace.options) == 16 and next(o for o in pace.options if o.default).value == "45|8"


def test_platforms_view_marks_state_and_disables_unconfigured():
    view = build_platforms_view(state())
    by_id = {c.custom_id: c for c in view.children}
    assert by_id["panel:plat-instagram"].label == "Reels ✅"
    assert by_id["panel:plat-tiktok"].label == "TikTok ⚪"
    assert by_id["panel:plat-youtube"].disabled is True
