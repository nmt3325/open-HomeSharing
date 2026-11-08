"""Music library backend for the Home Sharing server.

`Library` is the pluggable backend interface. The DAAP layer only calls the
methods below, so a Navidrome/Subsonic backend can replace FileLibrary later
without touching the protocol code (that is the planned plugin boundary).
"""
import hashlib
import os
import threading
import time

try:
    import mutagen
except ImportError:  # metadata falls back to file names
    mutagen = None

AUDIO_EXT = {
    ".mp3": ("mp3", "audio/mpeg"),
    ".m4a": ("m4a", "audio/mp4"),
    ".aac": ("m4a", "audio/mp4"),
    ".mp4": ("m4a", "audio/mp4"),
    ".alac": ("m4a", "audio/mp4"),
    ".aif": ("aif", "audio/aiff"),
    ".aiff": ("aif", "audio/aiff"),
    ".wav": ("wav", "audio/wav"),
}


# raw ID3 frames (WAV/AIFF files are not "easy" in mutagen) -> easy keys
_ID3MAP = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
           "genre": "TCON", "composer": "TCOM", "date": "TDRC", "tracknumber": "TRCK",
           "discnumber": "TPOS"}


def _easy(tags):
    if tags is None or not hasattr(tags, "getall"):
        return tags
    out = {}
    for k, fid in _ID3MAP.items():
        fr = tags.get(fid)
        if fr is not None and getattr(fr, "text", None):
            out[k] = [str(fr.text[0])]
    return out


def _first(tags, *keys):
    if not tags:
        return None
    for k in keys:
        v = tags.get(k)
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            if not v:
                continue
            v = v[0]
        if hasattr(v, "text"):
            t = v.text
            v = t[0] if t else None
        if v is None:
            continue
        return v
    return None


def _num(v):
    """'3/12' -> 3, (3, 12) -> 3."""
    if v is None:
        return 0
    if isinstance(v, (tuple, list)):
        v = v[0] if v else 0
    try:
        return int(str(v).split("/")[0].strip() or 0)
    except ValueError:
        return 0


class Track:
    __slots__ = ("id", "persistent_id", "path", "title", "artist", "album",
                 "album_artist", "genre", "composer", "year", "track_no",
                 "track_count", "disc_no", "disc_count", "duration_ms",
                 "bitrate", "samplerate", "size", "fmt", "mime", "mtime",
                 "album_id", "artist_id")

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def _stable_id64(s):
    return int.from_bytes(hashlib.sha1(s.encode("utf-8")).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF


class FileLibrary:
    """Scans a directory tree of audio files (read-only)."""

    def __init__(self, root, name="Home Sharing"):
        self.root = os.path.abspath(root)
        self.name = name
        self.tracks = {}          # id -> Track
        self.revision = 1
        self._lock = threading.Lock()
        self.scan()

    # ---- backend interface -------------------------------------------
    def all_tracks(self):
        return sorted(self.tracks.values(), key=lambda t: (t.album_artist.lower(), t.album.lower(), t.disc_no, t.track_no, t.title.lower()))

    def get(self, track_id):
        return self.tracks.get(track_id)

    def playlists(self):
        """[(id, name, [track ids], is_base)]; id 1 = base library playlist."""
        ids = [t.id for t in self.all_tracks()]
        return [(1, self.name, ids, True)]

    def groups(self, kind="albums"):
        """kind albums|artists -> [(group_id(64bit), name, artist, [track ids])]"""
        out = {}
        for t in self.all_tracks():
            if kind == "artists":
                key, name, art = t.artist_id, (t.album_artist or t.artist), ""
            else:
                key, name, art = t.album_id, t.album, t.album_artist
            g = out.setdefault(key, [key, name, art, []])
            g[3].append(t.id)
        return [tuple(g) for g in out.values()]

    # ---- scanning ----------------------------------------------------
    def scan(self):
        found = {}
        next_id = 1
        for dirpath, _dirs, files in os.walk(self.root):
            for fn in sorted(files):
                ext = os.path.splitext(fn)[1].lower()
                if ext not in AUDIO_EXT:
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    t = self._read(path, ext)
                except Exception as e:  # unreadable file: skip
                    print("[library] skip %s: %s" % (path, e))
                    continue
                t.id = next_id
                next_id += 1
                found[t.id] = t
        with self._lock:
            self.tracks = found
            self.revision += 1
        print("[library] %d tracks from %s" % (len(found), self.root))

    def _read(self, path, ext):
        st = os.stat(path)
        t = Track()
        t.path = path
        t.size = st.st_size
        t.mtime = int(st.st_mtime)
        t.fmt, t.mime = AUDIO_EXT[ext]
        rel = os.path.relpath(path, self.root)
        t.persistent_id = _stable_id64(rel)
        tags = None
        info = None
        if mutagen is not None:
            mf = mutagen.File(path, easy=True)
            if mf is not None:
                tags = _easy(mf.tags)
                info = mf.info
        base = os.path.splitext(os.path.basename(path))[0]
        parent = os.path.basename(os.path.dirname(path))
        t.title = str(_first(tags, "title") or base)
        t.artist = str(_first(tags, "artist") or "Unknown Artist")
        t.album = str(_first(tags, "album") or parent or "Unknown Album")
        t.album_artist = str(_first(tags, "albumartist", "album artist") or t.artist)
        t.genre = str(_first(tags, "genre") or "")
        t.composer = str(_first(tags, "composer") or "")
        t.year = _num(_first(tags, "date", "year", "originaldate"))
        t.track_no = _num(_first(tags, "tracknumber"))
        tn = _first(tags, "tracknumber")
        t.track_count = _num(str(tn).split("/")[1]) if tn and "/" in str(tn) else 0
        t.disc_no = _num(_first(tags, "discnumber"))
        t.disc_count = 0
        t.duration_ms = int((getattr(info, "length", 0) or 0) * 1000)
        t.bitrate = int((getattr(info, "bitrate", 0) or 0) / 1000)
        t.samplerate = int(getattr(info, "sample_rate", 0) or 0)
        t.album_id = _stable_id64("album:" + t.album_artist.lower() + "\0" + t.album.lower())
        t.artist_id = _stable_id64("artist:" + t.album_artist.lower())
        return t
