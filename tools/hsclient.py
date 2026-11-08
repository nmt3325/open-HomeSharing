"""DAAP / Home Sharing test client: walks the iTunes connection sequence
(docs/PROTOCOL section 9) against the server and checks each step, including
the FairPlay SAP v2.1 handshake on /fp-setup.

usage (from the repo root):
    python tools/hsclient.py [http://host:3689] [hsgid]
"""
import os
import socket
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request

# Allow "python tools/hsclient.py" from the repo root to find the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from homeshare.dmapdec import decode, dump, find, items

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3689"
HSGID = sys.argv[2] if len(sys.argv) > 2 else "00000000-0000-0000-0000-000000000000"
HDR = {"Client-DAAP-Version": "3.13", "Client-iTunes-Sharing-Version": "3.15",
       "Viewer-Only-Client": "1", "User-Agent": "Music/1.0 (iPhone; hsclient)"}
FP_HDR = b"FPLY\x02\x01"      # magic + Home Sharing SAP version 02 01
RESULTS = []


def req(path, method="GET", body=None, extra=None, timeout=10):
    r = urllib.request.Request(BASE + path, data=body, method=method,
                               headers={**HDR, **(extra or {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def check(name, ok, info=""):
    RESULTS.append(ok)
    print("[%s] %-34s %s" % ("OK" if ok else "NG", name, info))


def fp_setup():
    # P1 (client->server): 12-byte header (type 0x01) + payload 02 00 <idx> bb.
    idx = 1
    p1 = FP_HDR + b"\x01\x00" + struct.pack(">I", 4) + bytes([0x02, 0x00, idx, 0xbb])
    s, _h, b = req("/fp-setup", method="POST", body=p1,
                   extra={"Content-Type": "application/octet-stream"})
    p2_ok = (s == 200 and b[:6] == FP_HDR and b[6] == 0x02 and len(b) == 142
             and b[13] == idx)
    check("fp-setup P1 -> P2 (142B)", p2_ok, "status=%s len=%d" % (s, len(b)))
    # P3 (client->server): type 0x03 + 152-byte blob; server echoes the last 20.
    blob = os.urandom(152)
    p3 = FP_HDR + b"\x03\x00" + struct.pack(">I", len(blob)) + blob
    s, _h, b = req("/fp-setup", method="POST", body=p3,
                   extra={"Content-Type": "application/octet-stream"})
    p4_ok = (s == 200 and b[:6] == FP_HDR and b[6] == 0x04 and b[-20:] == blob[-20:])
    check("fp-setup P3 -> P4 (echo tail)", p4_ok, "status=%s len=%d" % (s, len(b)))


def main():
    Q = urllib.parse.quote
    s, h, b = req("/server-info")
    t = decode(b)
    check("server-info (msrv)", s == 200 and t[0][0] == "msrv",
          "minm=%s apro=%s aeSV=%s DAAP-Server=%s" % (
              find(t, "minm"), hex(find(t, "apro") or 0), hex(find(t, "aeSV") or 0),
              h.get("DAAP-Server")))
    # aeFP is deliberately not advertised; iOS Music runs fp-setup anyway.
    check("server-info has no aeFP", find(t, "aeFP") is None)
    s, h, b = req("/content-codes")
    check("content-codes (mccr)", s == 200 and decode(b)[0][0] == "mccr",
          "%d codes" % (len(find(decode(b), "mccr")) - 1))
    # FairPlay SAP handshake (iOS requires it regardless of aeFP).
    fp_setup()
    s, h, b = req("/login?hsgid=%s&hasFP=1" % HSGID)
    sid = find(decode(b), "mlid")
    check("login with hsgid (mlog)", s == 200 and bool(sid), "session-id=%s" % sid)
    q = "session-id=%d" % sid
    s, h, b = req("/update?%s&revision-number=1&hsgid=%s" % (q, HSGID))
    rev = find(decode(b), "musr")
    check("update (mupd)", s == 200 and bool(rev), "musr=%s" % rev)
    q += "&revision-number=%d" % rev
    # home-share-verify must answer 204 No Content, or iOS logs out.
    s, h, b = req("/home-share-verify?%s" % q)
    check("home-share-verify -> 204", s == 204, "status=%s" % s)
    s, h, b = req("/databases?" + q)
    dbs = items(decode(b))
    check("databases (avdb)", s == 200 and bool(dbs),
          str([(d.get("miid"), d.get("minm"), d.get("mimc")) for d in dbs]))
    meta = ("dmap.itemid,dmap.itemname,daap.songartist,daap.songalbum,daap.songtime,"
            "daap.songformat,daap.songsize,com.apple.itunes.mediakind")
    mk = Q("('com.apple.itunes.mediakind:1','com.apple.itunes.mediakind:32')")
    s, h, b = req("/databases/1/items?%s&type=music&meta=%s&query=%s" % (q, meta, mk))
    songs = items(decode(b))
    check("items (adbs)", s == 200 and len(songs) > 0,
          "%d: %s" % (len(songs), [(x.get("miid"), x.get("minm"), x.get("asar")) for x in songs]))
    s, h, b = req("/databases/1/items?%s&meta=%s&query=%s" % (q, meta, Q("'com.apple.itunes.mediakind:2'")))
    check("items movie query -> empty", s == 200 and not items(decode(b)))
    s, h, b = req("/databases/1/containers?%s&meta=dmap.itemid,dmap.itemname,daap.baseplaylist" % q)
    pls = items(decode(b))
    check("containers (aply)", s == 200 and bool(pls),
          str([(p.get("miid"), p.get("minm"), p.get("abpl")) for p in pls]))
    s, h, b = req("/databases/1/containers/%d/items?%s&meta=dmap.itemid,dmap.containeritemid" % (pls[0]["miid"], q))
    pli = items(decode(b))
    check("container items (apso)", s == 200 and len(pli) == len(songs),
          "%d items mcti=%s" % (len(pli), [x.get("mcti") for x in pli]))
    for g in ("albums", "artists"):
        s, h, b = req("/databases/1/groups?%s&group-type=%s&meta=dmap.itemname,dmap.itemcount" % (q, g))
        gr = items(decode(b))
        check("groups %s (%s)" % (g, decode(b)[0][0]), s == 200 and bool(gr),
              str([(x.get("minm"), x.get("mimc")) for x in gr]))
    song = songs[0]
    p = "/databases/1/items/%d.%s?%s" % (song["miid"], song.get("asfm", "mp3"), q)
    s, h, b = req(p, extra={"Range": "bytes=0-99"})
    check("stream Range 0-99 -> 206", s == 206 and len(b) == 100 and b[:4] == b"RIFF",
          "%s %s" % (h.get("Content-Range"), h.get("Content-Type")))
    s, h, b = req(p)
    check("stream full file", s == 200 and len(b) == song.get("assz"), "%d bytes" % len(b))
    try:
        req("/update?%s&delta=%d" % (q, rev), timeout=3)
        check("update long-poll held open", False, "returned immediately")
    except (TimeoutError, urllib.error.URLError, socket.timeout):
        check("update long-poll held open", True, ">3s, like iTunes")
    s, h, b = req("/databases?session-id=12345")
    check("bad session -> 403", s == 403)
    s, h, b = req("/logout?%s" % q)
    check("logout -> 204", s == 204)
    print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
    if "-v" in sys.argv:
        print("\n".join(dump(decode(req("/server-info")[2]))))


if __name__ == "__main__":
    main()
