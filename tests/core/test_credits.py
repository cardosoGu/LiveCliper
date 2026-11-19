from liveclips.core.credits import credit_text


def test_credit_per_platform():
    assert credit_text("twitch", "gaules") == "twitch.tv/gaules"
    assert credit_text("kick", "x") == "kick.com/x"
    assert credit_text("youtube", "Podpah") == "youtube.com/@Podpah"


def test_credit_text_youtube_channel_id_and_outro():
    assert credit_text("youtube", "channel/UCabc_1") == "youtube.com/channel/UCabc_1"
    assert credit_text("outro", "vimeo.com") == "vimeo.com"
