from liveclips.control.channels import CATEGORY, CHANNELS, ensure_channels


class FakeChannel:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name


class FakeGuild:
    def __init__(self):
        self.channels = {}
        self.categories = []
        self.next_id = 100

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def create_category(self, name):
        cat = FakeChannel(self.next_id, name)
        self.next_id += 1
        self.categories.append(cat)
        return cat

    async def create_text_channel(self, name, category=None):
        ch = FakeChannel(self.next_id, name)
        ch.category = category
        self.next_id += 1
        self.channels[ch.id] = ch
        return ch


async def test_creates_category_and_channels_once(db):
    g = FakeGuild()
    first = await ensure_channels(g, db)
    assert list(first) == list(CHANNELS)
    assert [c.name for c in g.categories] == [CATEGORY]
    assert all(ch.category is g.categories[0] for ch in first.values())
    again = await ensure_channels(g, db)
    assert {k: v.id for k, v in again.items()} == {k: v.id for k, v in first.items()}
    assert len(g.channels) == len(CHANNELS) and len(g.categories) == 1


async def test_recreates_deleted_channel_in_existing_category(db):
    g = FakeGuild()
    first = await ensure_channels(g, db)
    del g.channels[first["logs"].id]
    again = await ensure_channels(g, db)
    assert again["logs"].id != first["logs"].id
    assert len(g.categories) == 1
