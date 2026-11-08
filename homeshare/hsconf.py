"""Persistent identity + Bonjour service definitions (docs/PROTOCOL sections 1-2).

TXT keys/values follow hs_txt_builder (FUN_1406f0f40) / FUN_1406f0e00:
  hG = Home Sharing group id (lib+0xB6), hQ = %u (lib+0x94), hC = lib+0xC6,
  DvSv = 0xCDC, iCSV = 0x10004, DvTy = iTunes.
_home-sharing._tcp is registered as "_home-sharing._tcp.,_<hG>"
(FUN_1406f1f10), i.e. with the DNS-SD subtype _<hG>.

Per-install identity (Machine ID, Database ID, MID, hC and the learned hG)
is generated on first start and persisted to <state dir>/state.json.  The
state dir is, in order of precedence: --state-dir, $HS_STATE_DIR,
$XDG_STATE_HOME/homeshare, ~/.local/state/homeshare.  It is deliberately
kept outside the source tree so a checkout never carries machine identity.
"""
import json
import os
import secrets
import uuid


def default_state_dir():
    d = os.environ.get("HS_STATE_DIR")
    if d:
        return d
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "homeshare")


STATE_DIR = default_state_dir()
STATE = os.path.join(STATE_DIR, "state.json")


def set_state_dir(path):
    """Point state.json (and the default log) at another directory."""
    global STATE_DIR, STATE
    STATE_DIR = os.path.abspath(os.path.expanduser(path))
    STATE = os.path.join(STATE_DIR, "state.json")


def load_ids():
    try:
        with open(STATE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("machine_id", secrets.token_hex(6).upper())     # 48-bit
    d.setdefault("database_id", secrets.token_hex(8).upper())    # 64-bit
    d.setdefault("mid", secrets.token_hex(8).upper())            # "0x%llX"
    d.setdefault("hC", str(uuid.uuid4()))
    save_ids(d)
    return d


def save_ids(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, STATE)


def services(cfg, ids):
    txt = [("txtvers", "1"), ("dmv", "131085"), ("Version", "196621"),
           ("iTSh Version", "196623"), ("Machine ID", ids["machine_id"]),
           ("MID", "0x" + ids["mid"]), ("Database ID", ids["database_id"]),
           ("OSsi", "0x1F5"), ("Machine Name", cfg["name"]),
           ("Password", "0"), ("Media Kinds Shared", "1")]
    out = [{"name": cfg["name"], "type": "_daap._tcp", "port": cfg["port"], "txt": txt}]
    g = cfg.get("hsgid")
    if g:
        # live Apple _home-sharing TXT has no Password / Media Kinds Shared
        base = [kv for kv in txt if kv[0] not in ("Password", "Media Kinds Shared")]
        hs = base + [("hG", g), ("hQ", str(cfg.get("hq", 1))), ("hC", ids["hC"]),
                    ("DvSv", "3292"), ("iCSV", "65540"), ("DvTy", "iTunes")]
        out.append({"name": cfg["name"], "type": "_home-sharing._tcp",
                    "port": cfg["port"], "txt": hs, "subtypes": ["_" + g]})
    return out
