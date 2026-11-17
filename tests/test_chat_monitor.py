from liveclips.chat_monitor import ChannelInfo, ChatMonitor, parse_privmsg
from liveclips.spike import Spike


def test_parse_privmsg():
    line = ":fulano!fulano@fulano.tmi.twitch.tv PRIVMSG #Gaules :KKKKK clipa :)\r\n"
    assert parse_privmsg(line) == ("gaules", "KKKKK clipa :)")
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome") is None


class FakeDetector:
    def __init__(self):
        self.msgs = []
        self.next = None

    def add_message(self, ts, text):
        self.msgs.append((ts, text))

    def poll(self, now):
        s, self.next = self.next, None
        return s


async def test_lines_feed_detector_and_spike_calls_back():
    got = []

    async def on_spike(info, spike):
        got.append((info.login, spike.msgs))

    mon = ChatMonitor(on_spike, FakeDetector)
    mon.set_channels([ChannelInfo("gaules", "1", 5000), ChannelInfo("loud", "2", 900)])
    assert mon.handle_line("PING :tmi.twitch.tv", 0) == "PONG :tmi.twitch.tv"
    mon.handle_line(":a!a@a PRIVMSG #gaules :oi", 1.0)
    assert mon.detectors["gaules"].msgs == [(1.0, "oi")]
    assert mon.detectors["loud"].msgs == []
    mon.detectors["gaules"].next = Spike(at=2.0, ratio=5, keyword_bonus=0, msgs=40)
    await mon.check_spikes(2.0)
    assert got == [("gaules", 40)]


def test_set_channels_drops_gone_detectors():
    mon = ChatMonitor(lambda i, s: None, FakeDetector)
    mon.set_channels([ChannelInfo("a", "1", 1), ChannelInfo("b", "2", 1)])
    det_a = mon.detectors["a"]
    mon.set_channels([ChannelInfo("a", "1", 10)])
    assert set(mon.detectors) == {"a"}
    assert mon.detectors["a"] is det_a  # mantém o histórico de quem continua
    assert mon.channels["a"].viewers == 10
