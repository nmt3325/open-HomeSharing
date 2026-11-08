"""mDNS / DNS-SD side of the Home Sharing server.

* AvahiPublisher : registers services through the system avahi-daemon
  (D-Bus, via the pure-python `dbus-next`).  Supports DNS-SD subtypes, which
  Home Sharing needs: iTunes registers  "_home-sharing._tcp.,_<hG>"
  (FUN_1406f1f10) and clients browse  "_home-sharing._tcp.,_<own hG>"
  (FUN_1408830b0), i.e. discovery is filtered by the hG subtype.
* MdnsSniffer    : passive listener on 224.0.0.251:5353.  Prints every
  Home-Sharing related question/TXT seen on the LAN and reports hG values
  (from `_<hG>._sub._home-sharing._tcp` browse queries and `hG=` TXT keys).
"""
import asyncio
import socket
import struct
import threading
import time

AVAHI = "org.freedesktop.Avahi"
IF_UNSPEC = -1
PROTO_UNSPEC = -1


class AvahiPublisher:
    def __init__(self, log=print):
        self.log = log
        self.loop = asyncio.new_event_loop()
        self._bus = None
        self._group = None
        self._ready = threading.Event()
        self._err = None
        threading.Thread(target=self._run, name="avahi", daemon=True).start()
        self._ready.wait(10)
        if self._err:
            raise self._err

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._connect())
        except Exception as e:  # report to constructor
            self._err = e
            self._ready.set()
            return
        self._ready.set()
        self.loop.run_forever()

    async def _connect(self):
        from dbus_next.aio import MessageBus
        from dbus_next import BusType
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        intro = await self._bus.introspect(AVAHI, "/")
        obj = self._bus.get_proxy_object(AVAHI, "/", intro)
        self._server = obj.get_interface("org.freedesktop.Avahi.Server")
        self.hostname = await self._server.call_get_host_name_fqdn()

    async def _publish(self, services):
        if self._group is None:
            path = await self._server.call_entry_group_new()
            intro = await self._bus.introspect(AVAHI, path)
            obj = self._bus.get_proxy_object(AVAHI, path, intro)
            self._group = obj.get_interface("org.freedesktop.Avahi.EntryGroup")
            self._group.on_state_changed(self._on_state)
        else:
            await self._group.call_reset()
        for s in services:
            txt = [("%s=%s" % (k, v)).encode("utf-8") for k, v in s["txt"]]
            await self._group.call_add_service(
                IF_UNSPEC, PROTO_UNSPEC, 0, s["name"], s["type"], "", "", s["port"], txt)
            for sub in s.get("subtypes", ()):
                await self._group.call_add_service_subtype(
                    IF_UNSPEC, PROTO_UNSPEC, 0, s["name"], s["type"], "",
                    "%s._sub.%s" % (sub, s["type"]))
        await self._group.call_commit()

    def _on_state(self, state, error):
        names = {0: "uncommitted", 1: "registering", 2: "established", 3: "COLLISION", 4: "FAILURE"}
        self.log("[mdns] entry group state=%s %s" % (names.get(state, state), error or ""))

    def publish(self, services):
        """services: [{name, type, port, txt:[(k,v)...], subtypes:[...]}]"""
        fut = asyncio.run_coroutine_threadsafe(self._publish(services), self.loop)
        fut.result(15)
        for s in services:
            self.log("[mdns] published %r %s port %d subtypes=%s" % (
                s["name"], s["type"], s["port"], list(s.get("subtypes", ()))))


# --------------------------------------------------------------------------
# passive sniffer
# --------------------------------------------------------------------------

def _read_name(buf, off, depth=0):
    labels = []
    jumped = False
    end = off
    while True:
        if off >= len(buf) or depth > 20:
            raise ValueError("bad name")
        ln = buf[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            ptr = ((ln & 0x3F) << 8) | buf[off + 1]
            if not jumped:
                end = off + 2
            jumped = True
            off = ptr
            depth += 1
            continue
        off += 1
        labels.append(buf[off:off + ln].decode("utf-8", "replace"))
        off += ln
    if not jumped:
        end = off
    return ".".join(labels), end


def _parse(buf):
    qd, an, ns, ar = struct.unpack(">HHHH", buf[4:12])
    off = 12
    questions, records = [], []
    for _ in range(qd):
        name, off = _read_name(buf, off)
        qtype, _qc = struct.unpack(">HH", buf[off:off + 4])
        off += 4
        questions.append((name, qtype))
    for _ in range(an + ns + ar):
        name, off = _read_name(buf, off)
        rtype, _rc, _ttl, rdlen = struct.unpack(">HHIH", buf[off:off + 10])
        off += 10
        rdata = buf[off:off + rdlen]
        rdoff = off
        off += rdlen
        if rtype == 16:  # TXT
            items, i = [], 0
            while i < len(rdata):
                n = rdata[i]
                items.append(rdata[i + 1:i + 1 + n].decode("utf-8", "replace"))
                i += 1 + n
            records.append((name, "TXT", items))
        elif rtype == 12:  # PTR
            try:
                target, _ = _read_name(buf, rdoff)
            except ValueError:
                target = "?"
            records.append((name, "PTR", target))
    return questions, records


INTERESTING = ("_home-sharing._tcp", "_appletv-v2._tcp", "_touch-able._tcp", "_daap._tcp", "_dacp._tcp")


class MdnsSniffer(threading.Thread):
    def __init__(self, on_group=None, ignore_groups=(), log=print, own=None):
        super().__init__(name="mdns-sniffer", daemon=True)
        self.on_group = on_group
        self.ignore = set(g.upper() for g in ignore_groups if g)
        self.log = log
        self.seen = {}
        self.own = own

    def run(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            s.bind(("", 5353))
            mreq = struct.pack("4s4s", socket.inet_aton("224.0.0.251"), socket.inet_aton("0.0.0.0"))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as e:
            self.log("[sniff] disabled: %s" % e)
            return
        self.log("[sniff] listening on 224.0.0.251:5353")
        while True:
            try:
                buf, src = s.recvfrom(9000)
                qs, rs = _parse(buf)
            except Exception:
                continue
            for name, qtype in qs:
                if "._sub._home-sharing._tcp" in name:
                    grp = name.split("._sub.")[0].lstrip("_")
                    self._report(src[0], "browse-subtype", grp, name)
                elif any(t in name for t in INTERESTING):
                    self._note(src[0], "Q %s type=%d" % (name, qtype))
            for name, kind, data in rs:
                if kind == "TXT" and any(t in name for t in INTERESTING):
                    for kv in data:
                        if kv.startswith("hG="):
                            self._report(src[0], "txt-hG", kv[3:], name)
                    self._note(src[0], "TXT %s %s" % (name, data))

    def _note(self, src, msg):
        if self.own and self.own in msg and "HS GROUP" not in msg:
            return                      # our own announcements
        key = (src, msg)
        now = time.time()
        if now - self.seen.get(key, 0) > 300:
            self.seen[key] = now
            self.log("[sniff] %s %s" % (src, msg))

    def _report(self, src, how, grp, name):
        if not grp or grp.upper() in self.ignore:
            return
        self._note(src, "HS GROUP via %s: hG=%s (%s)" % (how, grp, name))
        if self.on_group:
            self.on_group(grp, src, how)
