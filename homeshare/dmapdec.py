"""Minimal DMAP decoder (for tests / debugging)."""
import struct

CONTAINERS = {"msrv", "mccr", "mdcl", "mlog", "mupd", "avdb", "adbs", "aply", "apso",
              "agal", "agar", "mlcl", "mlit", "abro", "abal", "abar"}
STRINGS = {"minm", "mcna", "asal", "asar", "asaa", "asgn", "ascp", "asfm", "msts"}


def decode(buf):
    out, i = [], 0
    while i + 8 <= len(buf):
        code = buf[i:i + 4].decode("latin1")
        ln = struct.unpack(">I", buf[i + 4:i + 8])[0]
        val = buf[i + 8:i + 8 + ln]
        i += 8 + ln
        if code in CONTAINERS:
            out.append((code, decode(val)))
        elif code in STRINGS:
            out.append((code, val.decode("utf-8", "replace")))
        elif ln in (1, 2, 4, 8):
            out.append((code, int.from_bytes(val, "big", signed=(code == "msto"))))
        else:
            out.append((code, val.hex()))
    return out


def find(tree, code):
    """first value with this code (depth-first)."""
    for c, v in tree:
        if c == code:
            return v
        if isinstance(v, list):
            r = find(v, code)
            if r is not None:
                return r
    return None


def items(tree):
    """list of dicts for each mlit under the first mlcl."""
    lst = find(tree, "mlcl") or []
    return [dict(v) for c, v in lst if c == "mlit"]


def dump(tree, ind=0):
    lines = []
    for c, v in tree:
        if isinstance(v, list):
            lines.append("  " * ind + c)
            lines.extend(dump(v, ind + 1))
        else:
            lines.append("  " * ind + "%s = %s" % (c, hex(v) if isinstance(v, int) and v > 9999 else v))
    return lines
