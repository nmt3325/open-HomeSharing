"""Song field table: (DAAP meta name, 4CC, Track attribute)."""

SONG_FIELDS = [
    ("dmap.persistentid", "mper", "persistent_id"),
    ("dmap.itemname", "minm", "title"),
    ("daap.songartist", "asar", "artist"),
    ("daap.songalbumartist", "asaa", "album_artist"),
    ("daap.songalbum", "asal", "album"),
    ("daap.songgenre", "asgn", "genre"),
    ("daap.songcomposer", "ascp", "composer"),
    ("daap.songtime", "astm", "duration_ms"),
    ("daap.songtracknumber", "astn", "track_no"),
    ("daap.songtrackcount", "astc", "track_count"),
    ("daap.songdiscnumber", "asdn", "disc_no"),
    ("daap.songyear", "asyr", "year"),
    ("daap.songformat", "asfm", "fmt"),
    ("daap.songsize", "assz", "size"),
    ("daap.songbitrate", "asbr", "bitrate"),
    ("daap.songsamplerate", "assr", "samplerate"),
    ("daap.songalbumid", "asai", "album_id"),
    ("daap.songartistid", "asri", "artist_id"),
]

# constant-valued fields (meta name, 4CC, value)
SONG_CONST = [
    ("com.apple.itunes.mediakind", "aeMK", 1),
    ("com.apple.itunes.extended-media-kind", "aeMk", 1),
    ("daap.songdatakind", "asdk", 0),
]
