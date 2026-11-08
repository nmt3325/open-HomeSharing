"""Browse DAAP / Home Sharing services the way a client would (python-zeroconf).

usage (from the repo root): python tools/mdns_check.py [hG ...]
  browses _daap._tcp, _home-sharing._tcp and, per hG, the subtype
  _<hG>._sub._home-sharing._tcp (what iTunes' browser FUN_1408830b0 asks for).
"""
import sys
import time

from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

HS = "_home-sharing._tcp.local."
TYPES = ["_daap._tcp.local.", HS] + ["_%s._sub.%s" % (g, HS) for g in sys.argv[1:]]


def main():
    zc = Zeroconf()
    found = {}

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            found.setdefault(service_type, set()).add(name)

    for t in TYPES:
        ServiceBrowser(zc, t, handlers=[on_change])
    time.sleep(5)
    for t in TYPES:
        names = sorted(found.get(t, []))
        print("%-60s -> %s" % (t, names or "(none)"))
        for n in names:
            base = HS if "._sub." in t else t
            info = zc.get_service_info(base, n, timeout=3000)
            if info is None:
                print("    (unresolved)")
                continue
            props = {k.decode(): (v or b"").decode("utf-8", "replace") for k, v in info.properties.items()}
            print("    %s port %d addrs %s" % (info.server, info.port, info.parsed_addresses()))
            if "._sub." not in t:
                print("    TXT %s" % props)
    zc.close()


if __name__ == "__main__":
    main()
