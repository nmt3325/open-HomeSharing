# iTunes Home Sharing protocol notes

Reverse-engineering notes behind this server, from static analysis of iTunes
12.13.12.3 (Windows x64, ImageBase `0x140000000`) with Ghidra, plus plaintext
observation of the protocol on a controlled LAN. Section numbers are referenced
from the source docstrings. FairPlay `/fp-setup` has its own write-up in
[`FAIRPLAY.md`](FAIRPLAY.md).

Home Sharing is **DAAP** (Digital Audio Access Protocol) — an HTTP service on TCP
3689 whose bodies are **DMAP** (tag-length-value) — wrapped with two extra
pieces: a Bonjour service type that carries a Home Sharing group id, and the
FairPlay `/fp-setup` + `/home-share-verify` authentication pair.

## 1. Bonjour / mDNS advertisement

- Service type `_home-sharing._tcp` on port 3689 (same HTTP server as DAAP).
  Related types iTunes also uses: `_daap._tcp`, `_dacp._tcp`,
  `_touch-able._tcp`, `_appletv-v2._tcp`.
- The TXT record always begins with `txtvers=1`. The rest is built by the TXT
  builder `FUN_1406f0f40` and emitted in dictionary order.
- `_home-sharing._tcp` is registered **with the DNS-SD subtype `_<hG>`**
  (`FUN_1406f1f10`), i.e. as `_<hG>._sub._home-sharing._tcp`. Clients browse the
  subtype of *their own* `hG` (`FUN_1408830b0`), so discovery is filtered by
  group: a phone only sees libraries whose `hG` matches its Apple ID's group.

See `homeshare/mdns.py` (publisher + a passive 5353 sniffer used by `--learn`)
and `homeshare/hsconf.py` (TXT construction).

## 2. `_home-sharing._tcp` TXT keys (generation order, `FUN_1406f0f40`)

```
txtvers       = 1
dmv           = 131085   (0x2000D)         %u
Version       = 196621   (0x3000D)         %u
iTSh Version  = 196623   (0x3000F)         %u
Machine ID    = 12 upper-hex chars (48-bit)
MID           = 0x%llX   (64-bit)
Database ID   = %llX     (library+0x88)
OSsi          = 0x%llX
Machine Name  = string
```

Then the builder branches on the share mode (`param_1`):

- plain DAAP share (`cx==1`): adds `Password` (`"1"`/`"0"`) and
  `Media Kinds Shared=%u`.
- Home-Sharing mode (`cx==0x40`): adds the group keys instead —
  `hG` (group GUID, library+0xB6), `hQ=%u` (library+0x94), `hC` (library+0xC6),
  and from `FUN_1406f0e00`: `DvSv=%u`, `iCSV=%u`, `DvTy=iTunes`.

A real device's `_home-sharing._tcp` TXT observed on the wire matched this exact
branch (group keys present, `Password` / `Media Kinds Shared` absent, no
`_daap._tcp`), confirming the `cx` split.

## 3. `hsgid` (client-side Home Sharing login GUID)

`hsgid` is what a *client* obtains to log into someone else's library:

- Endpoint `https://homesharing.itunes.apple.com/WebObjects/MZHomeSharing.woa/wa/getShareIdentifiers`.
- Request plist keys (`FUN_1404d8600`): `sgid`, `spid`, `dsid`, `guid`,
  `message-version`; headers `Viewer-Only-Client: 1`, `Content-Type: text/xml`.
- The response's `sgid` becomes the `hsgid` UUID.

A server does not need to mint this; it only needs to **accept** `hsgid=` on
incoming requests. This project's `--learn` instead adopts the `hG` already
advertised on the LAN so the library lands in the right group.

## 4. DAAP HTTP endpoints (server side)

Command dispatch tables are built by `FUN_140206650` (Home Sharing, 33 entries)
and `FUN_140214d20` (core, 67 entries); each entry is
`{u64 category; CFStringRef name; u32 cmdId; u32 flags; u64 rsv; handler}`.

| Path | iTunes handler | Behaviour |
|------|----------------|-----------|
| `/server-info` | `FUN_140a5bdb0` | DMAP `msrv`; see §6 for the values we emit |
| `/content-codes` | `FUN_140a5c8e0` | DMAP `mccr` content-code dictionary |
| `/login` | `FUN_140a5eda0` | `mlog{ mstt:200, mlid:<session-id> }`; accepts `hsgid=` / `pairing-guid=` |
| `/logout` | `FUN_140a5f020` | 204 |
| `/update` | cmd `0x10096` | `mupd{ musr:<revision> }`; long-polls until the library changes |
| `/databases` | cmd `0x10097` | list of databases (`avdb`) |
| `/databases/N/items` | cmd `0x10099` | song list (`adbs`) |
| `/databases/N/containers` | cmd `0x1009a` | playlists (`aply`) |
| `/databases/N/containers/M/items` | cmd `0x1009c` | playlist items (`apso`) |
| `/databases/N/groups` | cmd `0x1009b` | album/artist groups |
| `/databases/N/items/<id>.<ext>` | `FUN_140a5e040` | the audio stream (HTTP Range) |
| `/fp-setup` | `FUN_140a62b50` | FairPlay SAP — see [`FAIRPLAY.md`](FAIRPLAY.md) |
| `/home-share-verify` | `FUN_140a67490` | **must be 204** when the session is authorized |
| `/machine-id-verify` | `FUN_140a67600` | compares machine ids, 200/403 |

`flags` bit 0 means "requires a session", bit `0x10000` means "accepts a POST
body" (e.g. `items`, `fp-setup`).

## 5. `Client-Daap-Validation`

A header the client attaches to requests. Desktop iTunes computes it from the
request URL (`FUN_140dfe740` → `FUN_140586580` → `FUN_1405853b0`, an
MD5-family hash); iOS Music sends a 60-byte `FPLY 01 02 01` structure. A
server does not have to validate it; this project ignores it (accept-all).

## 6. server-info values this project emits

The client (`FUN_140e03270` accept logic) is picky about a few fields:

- `apro` (daap.protocolversion): major must be `3` → we send `0x3000D` (3.13).
- `aeSV` (music-sharing-version): major must be `3`; minor `>10` keeps groups
  (`asgr`) enabled → we send `0x3000F` (3.15). (OwnTone's `0x3000A` disables
  groups.)
- `ated=7` (extra data), `asgr=3` (artist+album groups), `mslr=1` (login
  required), `msup/mspi/msex/msbr/msqy/msix/msrs=1`, `msdc=1`.
- `aeFP` is **omitted**. Desktop iTunes then skips `/fp-setup` entirely
  (capability bit `0x1000` never set). iOS Music ignores `/server-info` and runs
  `/fp-setup` regardless — so omitting `aeFP` is harmless either way, and the
  FairPlay handler does the real work. See [`FAIRPLAY.md`](FAIRPLAY.md).

Implemented in `homeshare/daap.py`.

## 9. Client connection state machine (`FUN_140e03270`)

Desktop iTunes, connecting to a share (`state` at `conn+0x148`):

```
1  Bonjour resolve (FUN_140e06ce0)
2  GET /server-info  (cmd 0x32, expects msrv; FUN_140e03860)
3  GET /login        (cmd 0x33, expects mlog)
     on success: if aeFP bit (conn+0x1e0 bit12) == 0  -> connected (state 7)
                 else                                  -> state 4
4  POST /fp-setup    (FUN_140e03a80, SAP mode = conn+0x210 = aeFP value)
5  GET /home-share-verify (cmd 0x12d) -> connected
```

So a desktop client that never saw `aeFP` sends neither `/fp-setup` nor
`/home-share-verify`. **iOS Music does not follow this gating** — it issues
`POST /fp-setup` immediately after `/login`, unconditionally (see
[`FAIRPLAY.md`](FAIRPLAY.md)). Client request URLs are built by `FUN_140e03c80`;
`hsgid=` is appended to every request once a Home Sharing connection is active.

## 10. Routing in this project

`homeshare/handler.py` mirrors the dispatch tables above: a small set of exact
paths (`/server-info`, `/content-codes`, `/login`, `/fp-setup`,
`/machine-id-verify`, `/logout`, `/home-share-verify`, `/update`, `/databases`)
plus three regexes for `/databases/N/items`, `/databases/N/containers/M/items`,
and the `/databases/N/items/<id>.<ext>` stream. Session-guarded paths return 403
without a valid `session-id`. The audio stream honours HTTP `Range` (`206`).

## Media streaming

Home Sharing audio is served as **ordinary HTTP** with `Range` support — there
is no FairPlay content encryption on the stream (consistent with the SAP key
never being used, per [`FAIRPLAY.md`](FAIRPLAY.md)). `homeshare/library.py`
scans a folder, reads tags with mutagen when present, and maps each track to the
DMAP song fields in `homeshare/daap_fields.py`.
