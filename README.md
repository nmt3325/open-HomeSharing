# homeshare-linux

A from-scratch **Apple "Home Sharing" server for Linux** that an iPhone / iOS
Music app discovers, authenticates against, browses, and **plays audio from** —
with no iTunes, no Apple binaries, and **no captured FairPlay data**.

This is a clean-room re-implementation derived from static analysis of the
Windows iTunes binary (Ghidra) and plaintext observation of the on-the-wire
protocol. It implements the parts the open-source world had not reached:
`_home-sharing._tcp` advertisement, `hsgid` login, the FairPlay SAP `/fp-setup`
handshake, and `/home-share-verify`.

> Status: **working end-to-end on real hardware** — iPhone SE (iOS 15.8.7)
> discovers the Linux library, passes FairPlay, lists songs, and streams audio.

![iPhone playing from the Linux library (share name redacted)](docs/poc_playing.jpg)

## Why this is new

OwnTone/forked-daapd has long documented that *"Apple Home Sharing cannot be
supported"* and *"the iOS Music app uses Apple Home Sharing, which hasn't been
cracked."* The blocker was always the FairPlay `/fp-setup` step. This project
shows that, **for Home Sharing specifically, FairPlay SAP is only an
authentication ritual** and can be satisfied without any Apple cryptography.

## The key finding: FairPlay SAP is a no-op gate here

The `/fp-setup` handshake is 4 messages with a 12-byte header
(`FPLY` + version `02 01` + type + pad + big-endian length):

| Phase | Dir | Len | Payload |
|-------|-----|-----|---------|
| P1 | C→S | 16B | `02 00 <idx> bb` — **no client nonce** |
| P2 | S→C | 142B | `02 <idx>` + 128 bytes |
| P3 | C→S | 164B | 152-byte blob |
| P4 | S→C | 32B | 20 bytes = **echo of P3's last 20 bytes** |

Two facts make this trivial to satisfy on Linux:

1. **The client never validates P2.** Two genuine P2 responses captured from
   real iTunes share **0 of 128** blob bytes — the blob is pure per-session
   noise. P1 carries no client nonce, and Home Sharing streams media as plain
   HTTP, so the SAP-derived key is never used. We therefore **synthesize** P2 as
   `header + mode + idx + os.urandom(128)`. P4 is just an echo.
2. **`/home-share-verify` must return `204 No Content`.** Returning `200` makes
   the client treat the session as unverified and log out
   ("Unable to turn on Home Sharing").

No captured bytes ship in this repo. See [`docs/FAIRPLAY.md`](docs/FAIRPLAY.md)
for the full analysis and [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the rest
of the protocol.

## Minimal server contract (verified on device)

```
GET  /login                         -> 200  mlog{ mstt:200, mlid:<session> }
POST /fp-setup  (P1, type 0x01)     -> 200  FPLY 02 01 02 00 | 02 <idx> + random(128)
POST /fp-setup  (P3, type 0x03)     -> 200  FPLY 02 01 04 00 | echo last 20 bytes of P3
GET  /home-share-verify             -> 204  No Content           (critical)
GET  /databases                     -> 200  DMAP
GET  /databases/N/items/<id>.<ext>  -> 206  plain audio (HTTP Range)
```

`Client-Daap-Validation` headers ride on every client request; the server
ignores them (accept-all is sufficient).

## Quick start

```bash
# 1) deps: avahi-daemon for mDNS (driven over D-Bus via dbus-next),
#    mutagen for tags, zeroconf for --learn and tools/mdns_check.py
sudo apt install avahi-daemon
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 2) make a few test tracks (sine waves w/ tags), or point at your own folder
python tools/gen_testmusic.py testmusic

# 3) run
python -m homeshare.server --music testmusic --name "My Linux Library" --learn

# 4) optional self-test from another shell (full connection incl. FairPlay)
python tools/hsclient.py http://127.0.0.1:3689
```

Then on an iPhone signed into the **same Apple ID** with Home Sharing enabled:
Music → Library → Home Sharing → *My Linux Library*.

To keep it running as a systemd user service, see
[`systemd/homeshare.service`](systemd/homeshare.service).

### Options

| Flag | Env | Meaning |
|------|-----|---------|
| `--music DIR` | `HS_MUSIC` | folder to share: MP3, AAC/ALAC (M4A), AIFF, WAV |
| `--name NAME` | `HS_NAME` | library display name |
| `--port N` | `HS_PORT` | HTTP port (default 3689) |
| `--hsgid GUID` | `HS_HSGID` | Home Sharing group id (`hG`) to advertise |
| `--learn` | | sniff the LAN and adopt the `hG` of the local Home Sharing group |
| `--no-mdns` | | don't publish via Avahi (for manual testing) |
| `--state-dir DIR` | `HS_STATE_DIR` | where `state.json` + `hs.log` live (default `$XDG_STATE_HOME/homeshare`) |
| `--log FILE` | | log file (default `<state-dir>/hs.log`) |

`SIGHUP` (or `systemctl --user reload homeshare`) rescans the library.

`--learn` exists because a client only shows libraries whose `hG` (Home Sharing
group GUID) matches its account. The GUID is derived from the Apple ID; the
easiest way to match is to adopt the one already advertised on the LAN (e.g. by
iTunes / Music on a computer signed into the same account). Once learned it is
saved, so it only has to be seen once.

Per-install identity (Machine ID, Database ID, MID, `hC`, learned `hG`) is
generated randomly on first start and kept in `state.json` in the state
directory — outside the source tree, so a checkout never carries anyone's
identity.

## Layout

```
homeshare/          the server (importable package)
  server.py           entry point (python -m homeshare.server), mDNS wiring
  handler.py          HTTP routing + FairPlay SAP + home-share-verify
  daap.py             session state, server-info / login / update builders
  daap_lists.py       databases / containers / items / groups (DMAP lists)
  daap_fields.py      song metadata field table
  dmap.py             DMAP tag encoder
  dmapdec.py          DMAP decoder (used by the test client)
  library.py          music scanner + pluggable backend interface
  mdns.py             Avahi publisher + passive mDNS sniffer
  hsconf.py           per-install identity + Bonjour TXT records
  hslearn.py          active hG discovery (zeroconf)
tools/
  hsclient.py         self-test client: full connection incl. FairPlay
  mdns_check.py       verify the services are discoverable
  gen_testmusic.py    generate tagged sine-wave test tracks
systemd/
  homeshare.service   systemd --user unit
docs/
  PROTOCOL.md         DAAP / Bonjour / dispatch-table notes
  FAIRPLAY.md         the /fp-setup analysis
```

## Roadmap

- Pluggable library backends (Navidrome / Subsonic) under the DAAP layer —
  `homeshare/library.py` already isolates the backend interface.
- Artwork, playlists, and `/update` long-poll refinements.

## Credits / provenance

Clean-room analysis of iTunes 12.13.12 (Windows x64) with Ghidra, plus wire
observation on a controlled LAN with devices owned by the author. Intended for
interoperability and research. Licensed under the MIT License (see `LICENSE`).
