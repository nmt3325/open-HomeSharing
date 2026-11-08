"""Learn the Home Sharing group id (hG) from the LAN.

Two sources, both observable on the wire:
 1. active : DNS-SD browse of _home-sharing._tcp -> TXT hG of an existing
             iTunes / Music library signed in with the same Apple ID.
 2. passive: mdns.MdnsSniffer sees a client browsing
             _<hG>._sub._home-sharing._tcp (iOS Music / iTunes browser).
"""
import time

HS = "_home-sharing._tcp.local."


def browse_home_sharing(timeout=4.0, exclude=(), log=print):
    """-> [(instance, hG, [addresses], txt dict)]"""
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        log("[learn] python-zeroconf missing; active learn disabled")
        return []
    zc = Zeroconf()
    names = set()

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            names.add(name)

    ServiceBrowser(zc, HS, handlers=[on_change])
    time.sleep(timeout)
    out = []
    for n in sorted(names):
        if any(n.startswith(x + ".") for x in exclude if x):
            continue
        info = zc.get_service_info(HS, n, timeout=3000)
        if info is None:
            continue
        txt = {k.decode(): (v or b"").decode("utf-8", "replace") for k, v in info.properties.items()}
        if txt.get("hG"):
            out.append((n, txt["hG"], info.parsed_addresses(), txt))
            log("[learn] found %s hG=%s at %s" % (n, txt["hG"], info.parsed_addresses()))
    zc.close()
    return out
