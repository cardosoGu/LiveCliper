from liveclips.capture.twitch.chat import ChannelInfo, ChatMonitor, parse_privmsg
from liveclips.capture.spike import Spike


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


async def test_summary_counts_messages_and_spikes():
    got = []

    async def on_spike(info, spike):
        got.append(info.login)

    clock = [0.0]
    mon = ChatMonitor(on_spike, FakeDetector, clock=lambda: clock[0])
    mon.set_channels([ChannelInfo("gaules", "1", 5000), ChannelInfo("loud", "2", 900)])
    for _ in range(30):
        mon.handle_line(":a!a@a PRIVMSG #gaules :kkkk", 1.0)
    for _ in range(6):
        mon.handle_line(":a!a@a PRIVMSG #loud :oi", 1.0)
    mon.detectors["gaules"].next = Spike(at=2.0, ratio=5, keyword_bonus=1, msgs=30)
    await mon.check_spikes(60.0)
    s = mon.summary(60.0)
    assert "36 msgs/min" in s and "gaules 30/min" in s and "picos detectados desde o início: 1" in s


def test_commands_and_bots_do_not_count_as_hype():
    mon = ChatMonitor(lambda i, s: None, FakeDetector)
    mon.set_channels([ChannelInfo("gaules", "1", 5000)])
    mon.handle_line(":a!a@a PRIVMSG #gaules :!pokeidle catch", 1.0)
    mon.handle_line(":a!a@a PRIVMSG #gaules :  !pix", 1.0)
    mon.handle_line(":nightbot!nightbot@nightbot PRIVMSG #gaules :KKKKK", 1.0)
    mon.handle_line(":StreamElements!se@se PRIVMSG #gaules :kkkk", 1.0)
    mon.handle_line(":b!b@b PRIVMSG #gaules :KKKKKKK", 1.0)
    assert mon.detectors["gaules"].msgs == [(1.0, "KKKKKKK")]
