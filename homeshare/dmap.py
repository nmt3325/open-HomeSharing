"""Minimal DMAP/DAAP tag encoder.

DMAP wire format: each node is  [4-byte ASCII code][4-byte big-endian length][payload].
Container (list) payloads are concatenated child nodes.

Type table derived from OwnTone's dmap_fields.gperf (code -> logical type).
Only the subset used by this server is listed; unknown codes must be passed with
an explicit type.
"""
import struct

# logical type per 4CC (subset we emit)
TYPES = {
    # status / generic dmap
    "mstt": "uint", "mlid": "uint", "muty": "byte", "mtco": "uint", "mrco": "uint",
    "miid": "uint", "mper": "ulong", "minm": "str", "mimc": "uint", "mctc": "uint",
    "mikd": "byte", "mcti": "uint", "mcnm": "uint", "mcna": "str", "mcty": "short",
    "msrv": "list", "mlcl": "list", "mlit": "list", "mdcl": "list", "mccr": "list",
    "mupd": "list", "musr": "uint", "mstm": "uint", "msts": "str",
    "mslr": "byte", "msal": "byte", "msup": "byte", "mspi": "byte", "msex": "byte",
    "msbr": "byte", "msqy": "byte", "msix": "byte", "msrs": "byte", "msdc": "uint",
    "msau": "byte", "msas": "byte", "msed": "byte", "mstc": "date", "msto": "int",
    "meds": "uint",
    # daap
    "apro": "ver", "mpro": "ver", "ppro": "ver",
    "avdb": "list", "adbs": "list", "aply": "list", "apso": "list",
    "abro": "list", "abal": "list", "abar": "list", "abcp": "list", "abgn": "list",
    "mlog": "list", "mccr": "list",
    "abpl": "byte", "aeSV": "uint", "aeMQ": "byte", "aeFR": "byte",
    "aeTr": "byte", "aeSL": "byte", "aeSR": "byte", "aeSX": "ulong",
    "ated": "short", "asgr": "short", "asse": "ulong", "mscu": "ulong",
    "aeFP": "byte", "aeHV": "byte", "aeHD": "byte",
    # song fields
    "asal": "str", "asar": "str", "astm": "uint", "astn": "short", "astc": "short",
    "asfm": "str", "asdt": "str", "asgn": "str", "ascp": "str", "asyr": "short",
    "assz": "uint", "asbr": "short", "asdk": "byte", "asc3": "byte",
    "asct": "str", "ascm": "str", "ascn": "str", "assn": "str", "assa": "str",
    "asaa": "str", "asdb": "byte", "asco": "byte", "asbt": "short", "asdc": "short",
    "ascd": "uint", "asdn": "short",
    "mdbk": "uint", "aeMK": "byte", "aeMk": "uint", "assr": "uint", "agal": "list", "agar": "list", "agac": "uint", "aeHV": "byte", "aeSP": "byte", "aePS": "byte", "mpco": "uint", "meds": "uint", "asai": "ulong", "asri": "ulong", "meia": "uint", "mcnm": "uint",
    # container-item id
    "mcon": "list",
}


def _enc_value(typ, val):
    if typ == "byte":
        return struct.pack(">b" if val < 0 else ">B", val & 0xFF)
    if typ == "short":
        return struct.pack(">h", val) if val < 0 else struct.pack(">H", val)
    if typ in ("int",):
        return struct.pack(">i", val)
    if typ in ("uint",):
        return struct.pack(">I", val & 0xFFFFFFFF)
    if typ in ("ulong", "long"):
        return struct.pack(">Q", val & 0xFFFFFFFFFFFFFFFF)
    if typ == "ver":
        # 32-bit version word, e.g. 0x0003000D -> 00 03 00 0D
        return struct.pack(">I", val & 0xFFFFFFFF)
    if typ == "date":
        return struct.pack(">I", val & 0xFFFFFFFF)
    if typ == "str":
        if isinstance(val, str):
            return val.encode("utf-8")
        return bytes(val)
    if typ == "raw":
        return bytes(val)
    raise ValueError("unknown type %r" % typ)


def node(code, val, typ=None):
    """Encode one DMAP node. code is 4 chars. val is python value or, for
    lists, a pre-encoded bytes payload."""
    assert len(code) == 4, code
    if typ is None:
        typ = TYPES.get(code)
        if typ is None:
            raise KeyError("no type for %s; pass typ=" % code)
    if typ == "list":
        payload = val if isinstance(val, (bytes, bytearray)) else b"".join(val)
        payload = bytes(payload)
    else:
        payload = _enc_value(typ, val)
    return code.encode("ascii") + struct.pack(">I", len(payload)) + payload


def container(code, *children):
    """children: iterable of already-encoded bytes."""
    payload = b"".join(c for c in children if c)
    return code.encode("ascii") + struct.pack(">I", len(payload)) + payload


def status_ok():
    return node("mstt", 200)
