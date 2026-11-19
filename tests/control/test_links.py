from liveclips.control.discord_bot import parse_link_message


def test_parse_link_message():
    assert parse_link_message("https://youtu.be/abc foca nas histórias") == \
        ("https://youtu.be/abc", "foca nas histórias")
    assert parse_link_message("olha isso https://www.twitch.tv/videos/1") == \
        ("https://www.twitch.tv/videos/1", "olha isso")
    assert parse_link_message("sem link aqui") is None
    url, instr = parse_link_message("https://x.y " + "a" * 600)
    assert len(instr) == 500
