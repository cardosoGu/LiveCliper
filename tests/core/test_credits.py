from liveclips.core.credits import credit_text


def test_credit_per_platform():
    assert credit_text("twitch", "gaules") == "twitch.tv/gaules"
    assert credit_text("kick", "x") == "kick.com/x"
    assert credit_text("youtube", "Podpah") == "youtube.com/@Podpah"
