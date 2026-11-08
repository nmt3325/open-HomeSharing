#!/usr/bin/env python3
"""iTunes Home Sharing / DAAP server for Linux (no Wine, no iTunes code).

  python -m homeshare.server --music ~/Music                  # plain DAAP
  python -m homeshare.server --music ~/Music --hsgid <hG>     # + _home-sharing._tcp
  python -m homeshare.server --music ~/Music --learn          # adopt hG seen on LAN

Identity (state.json) and the log live in the state dir (see hsconf).
SIGHUP rescans the library (bumps the /update revision).
"""
import argparse
import os
import signal
import socket
import sys
from http.server import ThreadingHTTPServer

from . import daap
from . import hsconf
from . import hslearn
from . import mdns
from .handler import Handler
from .library import FileLibrary


class Server(ThreadingHTTPServer):
    daemon_threads = True
    address_family = socket.AF_INET6        # dual stack: iOS may pick IPv6

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass
        super().server_bind()

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            Handler.state.log("[net] %s closed the connection (%s)" % (client_address[0], type(e).__name__))
            return
        super().handle_error(request, client_address)


def main():
    env = os.environ.get
    ap = argparse.ArgumentParser(prog="python -m homeshare.server",
                                 description="iTunes Home Sharing server for Linux")
    ap.add_argument("--music", default=env("HS_MUSIC", "/srv/music"),
                    help="directory to share (default $HS_MUSIC or /srv/music)")
    ap.add_argument("--name", default=env("HS_NAME", socket.gethostname() + " Home Sharing"),
                    help="library name shown on the iPhone")
    ap.add_argument("--port", type=int, default=int(env("HS_PORT", "3689")))
    ap.add_argument("--hsgid", default=env("HS_HSGID"),
                    help="Home Sharing group id (hG) to advertise")
    ap.add_argument("--learn", action="store_true", help="adopt first hG sniffed on the LAN")
    ap.add_argument("--no-mdns", action="store_true", help="do not publish via Avahi")
    ap.add_argument("--state-dir", default=None,
                    help="where state.json / hs.log live (default $HS_STATE_DIR or "
                         "$XDG_STATE_HOME/homeshare)")
    ap.add_argument("--log", default=None, help="log file (default <state-dir>/hs.log)")
    a = ap.parse_args()

    if a.state_dir:
        hsconf.set_state_dir(a.state_dir)
    ids = hsconf.load_ids()                 # creates the state dir on first run
    logfile = a.log or os.path.join(hsconf.STATE_DIR, "hs.log")
    cfg = {"name": a.name, "port": a.port, "logfile": logfile, "database_id": ids["database_id"],
           "hsgid": a.hsgid or ids.get("hsgid")}
    lib = FileLibrary(a.music, a.name)
    st = daap.DaapState(lib, cfg)
    Handler.state = st
    httpd = Server(("::", a.port), Handler)
    st.log("[main] DAAP/HS on [::]:%d tracks=%d hG=%s state=%s" % (
        a.port, len(lib.tracks), cfg["hsgid"], hsconf.STATE))
    signal.signal(signal.SIGHUP, lambda *_: lib.scan())

    if not a.no_mdns:
        if a.learn and not cfg["hsgid"]:
            found = hslearn.browse_home_sharing(timeout=7, exclude=(a.name,), log=st.log)
            if found:
                cfg["hsgid"] = ids["hsgid"] = found[0][1]
                hsconf.save_ids(ids)
                st.log("[hs] adopted hG=%s from %s" % (found[0][1], found[0][0]))
        pub = mdns.AvahiPublisher(log=st.log)
        pub.publish(hsconf.services(cfg, ids))

        def on_group(grp, src, how):
            if cfg.get("hsgid") or not a.learn:
                return
            cfg["hsgid"] = ids["hsgid"] = grp
            hsconf.save_ids(ids)
            st.log("[hs] adopted hG=%s from %s (%s); re-publishing" % (grp, src, how))
            pub.publish(hsconf.services(cfg, ids))

        mdns.MdnsSniffer(on_group=on_group, log=st.log, own=a.name).start()
    httpd.serve_forever()


if __name__ == "__main__":
    main()
