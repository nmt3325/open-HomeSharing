# FairPlay SAP on Home Sharing (`/fp-setup`)

This is the analysis behind `homeshare/handler.py`'s `_fp_setup()`. It explains
why the iTunes "Home Sharing" FairPlay handshake can be satisfied on Linux with
**no Apple cryptography and no captured key material**.

> TL;DR For Home Sharing, `/fp-setup` is a four-message ceremony that the iOS
> client does **not** cryptographically verify. The server can answer P1 with a
> random 128-byte blob and answer P3 by echoing its last 20 bytes. The real
> authorization check is `/home-share-verify`, which must return **204**.

## Where it sits in the connection

iOS Music, right after `/login`, issues `POST /fp-setup` **unconditionally** —
it does not fetch `/server-info` first and does not care whether the server
advertises `aeFP`. (Desktop iTunes is different: its connection state machine,
`FUN_140e03270`, only runs `/fp-setup` when `/server-info` carried `aeFP`, bit
`0x1000` of the capability word. So desktop clients can be satisfied by omitting
`aeFP`, but iPhones cannot — the handshake has to actually work.)

Full Home Sharing sequence:

```
GET  /server-info          (iOS skips this; desktop uses it to gate FairPlay)
GET  /login            -> session-id
POST /fp-setup  (P1)   -> P2          FairPlay SAP, see below
POST /fp-setup  (P3)   -> P4
GET  /home-share-verify -> 204 No Content
GET  /update / /databases / ...                 (normal DAAP)
GET  /databases/N/items/<id>.<ext> -> 206       (plain HTTP audio)
```

## Wire format

Every `/fp-setup` message, both directions, is a 12-byte header followed by a
payload:

```
offset  size  field
0       4     magic   "FPLY"  (46 50 4C 59)
4       2     version 02 01   <- Home Sharing. (AirPlay FairPlay uses 03 01.)
6       1     type    01=P1, 02=P2, 03=P3, 04=P4
7       1     pad     00
8       4     length  big-endian length of the payload that follows
12      ...   payload
```

The `02 01` vs `03 01` version split matters: Home Sharing SAP and AirPlay SAP
are **not interchangeable**, so existing AirPlay tools (playfair / shairport
blobs) cannot be reused directly here.

### The four messages

| Phase | Dir | Total | Payload | Notes |
|-------|-----|-------|---------|-------|
| P1 | C→S | 16B  | `02 00 <idx> bb` | `idx` = per-launch counter; **no client nonce** |
| P2 | S→C | 142B | `02 <idx>` + 128 bytes | 128-byte blob |
| P3 | C→S | 164B | 152-byte blob | |
| P4 | S→C | 32B  | 20 bytes | = **echo of P3's last 20 bytes** |

`idx` is a small integer the client increments each time the Music app launches.
Like genuine iTunes, the server echoes it back in P2 at payload offset 1
(byte 13 of the message). Everything after that is the opaque 128-byte blob.

## Why no crypto is needed

Two independent observations collapse this from "defeat FairPlay" to "return the
right shaped bytes".

1. **The client never validates the P2 blob.** Two genuine P2 responses,
   captured from a real iTunes server across two different sessions (`idx=0` and
   `idx=2`), share **0 of their 128** blob bytes — i.e. the blob is pure
   per-session entropy with no structure the client re-derives. Replaying a
   single captured P2 worked on real hardware across *different* `idx` sessions;
   and synthesizing a fresh `os.urandom(128)` blob works identically. So the
   server produces P2 as:

   ```
   FPLY 02 01 02 00 | len=130 | <mode> <idx> | os.urandom(128)
   ```

2. **The SAP-derived key is never exercised.** P1 carries no client nonce, and
   Home Sharing streams media over **plain HTTP** (ordinary `206` range GETs — no
   per-file content key, no encryption). There is simply nothing in the session
   that consumes a negotiated FairPlay key, so a "wrong" key is indistinguishable
   from a "right" one.

P4 is a trivial transformation of P3:

```
FPLY 02 01 04 00 | len=20 | <last 20 bytes of P3's payload>
```

On the iTunes side this ceremony is real: the server-side handler
`h_fp_setup` (`FUN_140a62b50`) calls a CoreFP wrapper (`FUN_1404e7490`) and the
obfuscated `FPSAPExchange` (`FUN_1400731d0`) to *generate* P2 with genuine
crypto. The point of this document is that the **client half never checks that
work**, so a server is free to skip it.

## The real gate: `/home-share-verify`

After P1–P4, the client calls `GET /home-share-verify?session-id=...`. This —
not FairPlay — is what decides whether Home Sharing turns on:

- It must return **`204 No Content`**.
- Returning `200` (even with a valid-looking body) makes the client treat the
  session as unverified and immediately `logout`, surfacing **"Unable to turn on
  Home Sharing"** on the phone.

Genuine iTunes also answers `204` here (`h_home_share_verify`,
`FUN_140a67490`).

## `Client-Daap-Validation`

Every iOS client request also carries a `Client-Daap-Validation` header (a
60-byte `FPLY 01 02 01` structure). The server **ignores it** — accept-all is
sufficient; nothing downstream depends on it.

## Consequences

- No Apple binaries, no CoreFP, and no captured traffic are required to serve
  iOS Home Sharing clients. This repository ships **none** of those.
- Because the "security" here is a shaped-bytes ceremony rather than a key
  exchange, this is best understood as an **interoperability / authentication
  stub**, not a break of FairPlay's content-protection crypto (which Home
  Sharing audio never invokes in the first place).

## Source references (iTunes 12.13.12, Windows x64)

| Symbol | Address | Role |
|--------|---------|------|
| client connection state machine | `FUN_140e03270` | sequences server-info → login → fp-setup → verify |
| client URL builder | `FUN_140e03c80` | emits `POST /fp-setup?session-id=..&hsgid=..` |
| server `/fp-setup` handler | `FUN_140a62b50` | calls CoreFP to build P2/P4 |
| CoreFP SAP wrapper / ctx | `FUN_1404e7490` / `FUN_1404e7370` | shared by DAAP server, DAAP client, AirPlay |
| `FPSAPExchange` | `FUN_1400731d0` | the obfuscated SAP exchange itself |
| server `/home-share-verify` | `FUN_140a67490` | returns 204 when the session is authorized |
| server `/machine-id-verify` | `FUN_140a67600` | compares machine ids |
