"""DAAP / Home Sharing protocol state and DMAP response builders.

Implements the server-side endpoints that iTunes' command dispatch tables
expose (section 10). server-info omits aeFP: desktop iTunes then skips
fp-setup entirely, while iOS Music runs the FairPlay SAP handshake
regardless (handled in handler.py, see docs/FAIRPLAY.md). Everything is
derived from the iTunes.exe analysis; the DMAP tag values for server-info
come from FUN_140a5bdb0.
"""
import os
import re
import socket
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import dmap

SERVER_HEADER = "iTunes/12.13.12 (Windows)"   # matches "DAAP-Server: iTunes/%s (Windows)"


def _ver(major, minor):
    return (major << 16) | minor


class DaapState:
    """Holds the library + runtime config shared by all requests."""

    def __init__(self, library, cfg):
        self.library = library
        self.cfg = cfg
        self._sessions = {}
        self._next_session = 0x10000001
        self._lock = threading.Lock()
        self._loglock = threading.Lock()

    def new_session(self):
        with self._lock:
            sid = self._next_session
            self._next_session += 1
            self._sessions[sid] = {"created": time.time(), "revision": 1}
        return sid

    def valid_session(self, sid):
        return sid in self._sessions

    def log(self, msg):
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
        print(line, flush=True)
        lf = self.cfg.get("logfile")
        if lf:
            with self._loglock:
                with open(lf, "a", encoding="utf-8") as f:
                    f.write(line + "\n")


# --------------------------------------------------------------------------
# server-info  (FUN_140a5bdb0) -- note: NO aeFP (see module docstring)
# --------------------------------------------------------------------------

def build_server_info(state):
    cfg = state.cfg
    lib = state.library
    n = dmap.node
    kids = [
        n("mstt", 200),
        n("mpro", _ver(2, 13)),              # dmap.protocolversion 0x2000d
        n("minm", cfg["name"]),              # server name
        n("apro", _ver(3, 13)),              # daap.protocolversion 0x3000d
        n("aeSV", _ver(3, 15)),              # music-sharing-version 0x3000f (minor>10 keeps groups)
        n("ated", 7),                        # supportsextradata
        n("asgr", 3),                        # supportsgroups: artists+albums
        n("aeMQ", 1),
        n("mscu", 0x3F),
        n("aeFR", 100),
        n("aeTr", 1), n("aeSL", 1), n("aeSR", 1),
        n("aeSX", 0x26F),
        # aeFP omitted: desktop clients skip fp-setup; iOS runs it anyway
        n("ppro", _ver(2, 1)),               # dpap.protocolversion 0x20001
        n("msed", 1),
        n("mslr", 1),                        # loginrequired
        n("mstm", 0x708),                    # timeout 1800s
        n("msal", 1),
        n("msas", 3),                        # auth schemes
        n("msup", 1), n("mspi", 1), n("msex", 1), n("msbr", 1),
        n("msqy", 1), n("msix", 1), n("msrs", 1),
        n("msdc", 1),                        # databases count
        n("mstc", int(time.time())),         # server utc time
        n("msto", time.localtime().tm_gmtoff),  # utc offset seconds
    ]
    if cfg.get("password"):                  # msau only when a password is set
        kids.insert(kids.index(n("msas", 3)), n("msau", 2))
    return dmap.container("msrv", *kids)


# --------------------------------------------------------------------------
# content-codes  (FUN_140a5c8e0)
# --------------------------------------------------------------------------

_DMAP_TYPE_CODE = {
    "byte": 1, "ubyte": 1, "short": 3, "ushort": 3, "int": 5, "uint": 5,
    "long": 7, "ulong": 7, "str": 9, "date": 10, "ver": 11, "list": 12,
}
# wire type numbers per DMAP spec: 1 ubyte 2 byte 3 ushort 4 short 5 uint
# 6 int 7 ulong 8 long 9 str 10 date 11 version 12 list
_DMAP_WIRE = {
    "byte": 2, "short": 4, "int": 6, "uint": 5, "ulong": 7, "long": 8,
    "str": 9, "date": 10, "ver": 11, "list": 12,
}


def build_content_codes():
    n = dmap.node
    entries = []
    for code, typ in dmap.TYPES.items():
        wire = _DMAP_WIRE.get(typ, 9)
        name = _CODE_NAMES.get(code, code)
        dct = dmap.container(
            "mdcl",
            n("mcnm", struct.unpack(">I", code.encode("ascii"))[0], typ="uint"),
            n("mcna", name),
            n("mcty", wire),
        )
        entries.append(dct)
    return dmap.container("mccr", n("mstt", 200), *entries)


# minimal human names (only a few matter; others default to the 4CC)
_CODE_NAMES = {
    "mstt": "dmap.status", "miid": "dmap.itemid", "minm": "dmap.itemname",
    "mper": "dmap.persistentid", "mcon": "dmap.container", "mcti": "dmap.containeritemid",
    "mimc": "dmap.itemcount", "mctc": "dmap.containercount", "mlcl": "dmap.listing",
    "mlit": "dmap.listingitem", "mikd": "dmap.itemkind", "asal": "daap.songalbum",
    "asar": "daap.songartist", "astm": "daap.songtime", "astn": "daap.songtracknumber",
}


# --------------------------------------------------------------------------
# login / update / databases / containers / items / groups
# --------------------------------------------------------------------------

def build_login(session_id):
    # mlog { mstt:200, mlid:session }   (FUN_140a5eda0)
    return dmap.container("mlog", dmap.node("mstt", 200), dmap.node("mlid", session_id))


def build_update(revision):
    # mupd { mstt:200, musr:revision }  (FUN_140a5f110)
    return dmap.container("mupd", dmap.node("mstt", 200), dmap.node("musr", revision))


def build_databases(state):
    lib = state.library
    n = dmap.node
    count = len(lib.tracks)
    db = dmap.container(
        "mlit",
        n("miid", 1),
        n("mper", int(state.cfg.get("database_id", "1"), 16), typ="ulong"),  # = TXT Database ID
        n("minm", state.cfg["name"]),
        n("mimc", count),               # song count
        n("mctc", len(lib.playlists())),  # playlist count
    )
    return dmap.container(
        "avdb",
        n("mstt", 200), n("muty", 0),
        n("mtco", 1), n("mrco", 1),
        dmap.container("mlcl", db),
    )
