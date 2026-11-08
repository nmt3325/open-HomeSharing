"""DAAP listing builders: containers / items / groups."""
from . import dmap
from .daap_fields import SONG_FIELDS, SONG_CONST

n = dmap.node
C = dmap.container


def _listing(tag, items):
    return C(tag, n("mstt", 200), n("muty", 0),
             n("mtco", len(items)), n("mrco", len(items)), C("mlcl", *items))


def build_containers(state):
    # aply { mstt muty mtco mrco mlcl{ mlit... } }   (FUN_140a5d860)
    items = []
    for pid, name, ids, is_base in state.library.playlists():
        kids = [n("miid", pid), n("mper", pid, typ="ulong"), n("minm", name),
                n("mimc", len(ids)), n("mpco", 0)]
        if is_base:
            kids.append(n("abpl", 1))
        items.append(C("mlit", *kids))
    return _listing("aply", items)


def _song(t, meta, cti=None):
    kids = [n("mikd", 2), n("miid", t.id)]
    for name, code, attr in SONG_FIELDS:
        if meta is None or name in meta:
            v = getattr(t, attr)
            if isinstance(v, str) and not v:
                continue
            kids.append(n(code, v))
    for name, code, val in SONG_CONST:
        if meta is None or name in meta:
            kids.append(n(code, val))
    if cti is not None:
        kids.append(n("mcti", cti))
    return C("mlit", *kids)


def build_items(tracks, meta, tag="adbs", with_cti=False):
    # adbs = database items (FUN_140a5d660), apso = playlist items (FUN_140a5de30)
    return _listing(tag, [_song(t, meta, t.id if with_cti else None) for t in tracks])


def build_groups(state, kind):
    # agal = albums, agar = artists  (FUN_140a5dc50)
    items = []
    for gid, name, artist, ids in state.library.groups(kind):
        kids = [n("miid", gid & 0xFFFFFFFF), n("mper", gid), n("minm", name or ""),
                n("mimc", len(ids))]
        if artist:
            kids.append(n("asaa", artist))
        items.append(C("mlit", *kids))
    return _listing("agar" if kind == "artists" else "agal", items)


def parse_meta(qs):
    m = qs.get("meta", [""])[0]
    if not m or m == "all":
        return None
    return set(x.strip() for x in m.split(","))
