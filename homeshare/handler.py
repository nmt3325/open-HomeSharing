"""HTTP routing for the DAAP / Home Sharing server (dispatch tables, section 10)."""
import os
import re
import time
import struct
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import daap
from . import daap_lists as L
from . import dmap

DMAP_CT = "application/x-dmap-tagged"
R_DB = re.compile(r"^/databases/(\d+)/(items|containers|groups|browse/\w+)$")
R_PL = re.compile(r"^/databases/(\d+)/containers/(\d+)/items$")
R_SONG = re.compile(r"^/databases/(\d+)/items/(\d+)\.(\w+)$")
R_ART = re.compile(r"^/databases/(\d+)/items/(\d+)/extra_data/artwork$")
R_MK = re.compile(r"com\.apple\.itunes\.(?:extended-)?mediakind:(\d+)")
NEED_SESSION = ("/update", "/databases", "/logout", "/home-share-verify")

# FairPlay SAP v2.1 (/fp-setup), see docs/FAIRPLAY.md.
# For Home Sharing this is a pure auth ritual: P1 carries no client nonce,
# media streams as plain HTTP, and the iOS client never cryptographically
# validates the P2 blob (two genuine iTunes P2s share 0/128 bytes). So P2 is
# SYNTHESIZED from fresh random bytes and P4 echoes the tail of P3 -- no
# Apple key material and no captured traffic is needed.
FP_HDR = b"FPLY\x02\x01"   # magic + version 02 01 (Home Sharing; AirPlay uses 03 01)
FP_P2_BLOB_LEN = 128        # observed length of the per-session blob


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = None   # set by server
    server_version = "iTunes/12.13.12"   # no BaseHTTP fingerprint
    sys_version = ""

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        self.state.log("[http] %s %s" % (self.client_address[0], fmt % args))

    # ---- helpers -----------------------------------------------------
    def _send(self, body, status=200, ctype=DMAP_CT, extra=None):
        self.send_response(status)
        self.send_header("DAAP-Server", daap.SERVER_HEADER)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _status(self, code):
        self._send(b"", status=code)

    def _fp_send(self, body):
        self.send_response(200)
        self.send_header("DAAP-Server", daap.SERVER_HEADER)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _fp_setup(self):
        st = self.state
        body = getattr(self, "_body", b"")
        if len(body) < 12 or body[:4] != b"FPLY":
            st.log("[FAIRPLAY] non-FPLY fp-setup (%d B) -> 403" % len(body))
            return self._status(403)
        msgtype = body[6]
        payload = body[12:]
        if msgtype == 0x01:
            mode = payload[0] if len(payload) >= 1 else 2
            idx = payload[2] if len(payload) >= 3 else 0
            # Synthesize P2: header + mode + idx + 128 random bytes. The client
            # echoes idx back in P3 but never checks the blob contents.
            blob = bytes([mode, idx]) + os.urandom(FP_P2_BLOB_LEN)
            resp = FP_HDR + b"\x02\x00" + struct.pack(">I", len(blob)) + blob
            st.log("[FAIRPLAY] P1 idx=%d mode=0x%02x -> synth P2 (%d B)" % (
                idx, mode, len(resp)))
            return self._fp_send(resp)
        if msgtype == 0x03:
            tail = payload[-20:]
            resp = FP_HDR + b"\x04\x00" + struct.pack(">I", len(tail)) + tail
            st.log("[FAIRPLAY] P3 (%d B) -> P4 echo tail (%d B)" % (len(body), len(resp)))
            return self._fp_send(resp)
        st.log("[FAIRPLAY] unexpected msgtype=0x%02x (%d B) -> 403" % (msgtype, len(body)))
        return self._status(403)

    def _trace(self, path, qs):
        hdr = {k: v for k, v in self.headers.items() if k.lower().startswith(("client-", "x-", "viewer-"))}
        keys = sorted(qs.keys())
        self.state.log("[req] %s %s q=%s hdr=%s" % (self.command, path, keys, hdr))
        for k in ("hsgid", "hspid", "pairing-guid", "session-id"):
            if k in qs:
                self.state.log("[req]   %s=%s" % (k, qs[k][0]))

    # ---- routing (dispatch table, docs/PROTOCOL section 10) -----------
    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        self._body = self.rfile.read(ln) if ln else b""
        self.do_GET()

    def do_GET(self):
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # keep the server alive, log the bug
            self.state.log("[error] %s %s: %r" % (self.command, self.path, e))
            try:
                self._status(500)
            except Exception:
                pass

    def _route(self):
        st = self.state
        u = urllib.parse.urlsplit(self.path)
        path = u.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        self._trace(path, qs)
        if path == "/server-info":
            return self._send(daap.build_server_info(st))
        if path == "/content-codes":
            return self._send(daap.build_content_codes())
        if path == "/login":
            sid = st.new_session()
            st.log("[auth] login -> session %d (hsgid=%s)" % (sid, qs.get("hsgid", ["-"])[0]))
            return self._send(daap.build_login(sid))
        if path == "/fp-setup":
            return self._fp_setup()
        if path == "/machine-id-verify":
            return self._status(200)
        sid = int(qs.get("session-id", ["0"])[0] or 0)
        if path.startswith(NEED_SESSION) and not st.valid_session(sid):
            st.log("[auth] bad session %d for %s -> 403" % (sid, path))
            return self._status(403)
        if path == "/logout":
            return self._status(204)
        if path == "/home-share-verify":
            return self._status(204)
        if path == "/update":
            return self._update(qs)
        if path == "/databases":
            return self._send(daap.build_databases(st))
        m = R_SONG.match(path)
        if m:
            return self._stream(int(m.group(2)))
        m = R_PL.match(path)
        if m:
            return self._pl_items(int(m.group(2)), qs)
        m = R_DB.match(path)
        if m:
            return self._db(m.group(2), qs)
        if R_ART.match(path):
            return self._status(404)          # artwork not implemented yet
        st.log("[req] unhandled %s %s -> 404" % (self.command, path))
        return self._status(404)

    # ---- endpoint bodies ------------------------------------------------
    def _update(self, qs):
        lib = self.state.library
        want = int(qs.get("revision-number", ["0"])[0] or 0)
        if want and want >= lib.revision:
            # long-poll: iTunes holds /update open until the library changes
            deadline = time.time() + self.state.cfg.get("update_wait", 1700)
            while lib.revision <= want and time.time() < deadline:
                time.sleep(1)
        return self._send(daap.build_update(lib.revision))

    def _filter(self, tracks, qs):
        q = qs.get("query", [""])[0]
        if not q:
            return tracks
        kinds = set(int(x) for x in R_MK.findall(q))
        if kinds and "!:" not in q and 1 not in kinds:
            self.state.log("[req] query %s -> no music, empty list" % q)
            return []
        return tracks

    def _db(self, what, qs):
        st = self.state
        meta = L.parse_meta(qs)
        if what == "items":
            return self._send(L.build_items(self._filter(st.library.all_tracks(), qs), meta, "adbs"))
        if what == "containers":
            return self._send(L.build_containers(st))
        if what == "groups":
            kind = qs.get("group-type", ["albums"])[0]
            return self._send(L.build_groups(st, kind))
        st.log("[req] %s not implemented -> 404" % what)
        return self._status(404)

    def _pl_items(self, pid, qs):
        lib = self.state.library
        for p, _name, ids, _base in lib.playlists():
            if p == pid:
                tr = [t for t in (lib.get(i) for i in ids) if t]
                return self._send(L.build_items(self._filter(tr, qs), L.parse_meta(qs), "apso", True))
        return self._status(404)

    def _stream(self, tid):
        # /databases/1/items/<id>.<ext>  -- plain file, Range -> 206
        t = self.state.library.get(tid)
        if t is None or not os.path.isfile(t.path):
            return self._status(404)
        size = os.path.getsize(t.path)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].strip().partition("-")
            if a:
                start = int(a)
                if b:
                    end = min(int(b), size - 1)
            elif b:
                start = max(0, size - int(b))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("DAAP-Server", daap.SERVER_HEADER)
        self.send_header("Content-Type", t.mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if self.command == "HEAD":
            return
        self.state.log("[stream] #%d %s bytes %d-%d/%d" % (tid, os.path.basename(t.path), start, end, size))
        with open(t.path, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
