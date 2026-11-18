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


async def test_uses_channels_given_by_id_without_creating(db):
    g = FakeGuild()
    for cid, name in ((11, "aprovacao-clipes"), (12, "realtime"), (13, "links"), (14, "logs")):
        g.channels[cid] = FakeChannel(cid, name)
    preset = {"aprovação": 11, "painel": 12, "gerar-clipes": 13, "logs": 14}
    found = await ensure_channels(g, db, preset)
    assert {k: v.id for k, v in found.items()} == preset
    assert g.categories == [] and len(g.channels) == 4  # nada foi criado
    assert db.kv_get("discord_channel:aprovação") == "11"


async def test_preset_id_that_does_not_exist_falls_back_to_creating(db):
    g = FakeGuild()
    g.channels[14] = FakeChannel(14, "logs")
    found = await ensure_channels(g, db, {"logs": 14, "aprovação": 999})
    assert found["logs"].id == 14
    assert found["aprovação"].id not in (999, 14) and found["aprovação"].name == "aprovação"
