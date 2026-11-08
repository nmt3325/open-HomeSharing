"""homeshare - an iTunes "Home Sharing" server for Linux.

Implements the DAAP 3.13 / DMAP protocol plus the Home Sharing
FairPlay SAP v2.1 handshake (/fp-setup) and /home-share-verify, so that
an iPhone's Music app lists this host under Library -> Home Sharing and
can browse and stream its music library.

Run with:  python -m homeshare.server --music <dir> --name "<library name>"
"""

__version__ = "0.1.0"
