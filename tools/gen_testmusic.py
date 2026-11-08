"""Generate a tiny tagged test library (sine-tone WAV + ID3) for testing.

usage (from the repo root):
    python tools/gen_testmusic.py [DIR]          (default ./testmusic)

Needs no audio encoder: tracks are PCM WAV with ID3 chunks (mutagen).
"""
import math
import os
import struct
import sys
import wave

from mutagen.id3 import TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
from mutagen.wave import WAVE

ROOT = sys.argv[1] if len(sys.argv) > 1 else "testmusic"
ALBUMS = [
    ("PoC Artist", "Sine Waves", "Electronic", "2024",
     [("A4 440Hz", 440.0), ("E5 659Hz", 659.25), ("A5 880Hz", 880.0)]),
    ("テスト アーティスト", "日本語アルバム", "J-Pop", "2025",
     [("ド", 261.63), ("ミ", 329.63), ("ソ", 392.0)]),
]


def tone(path, freq, secs=8, rate=44100):
    n = int(secs * rate)
    buf = bytearray()
    for i in range(n):
        env = min(1.0, i / 2000.0, (n - i) / 2000.0)
        v = int(12000 * env * math.sin(2 * math.pi * freq * i / rate))
        buf += struct.pack("<hh", v, v)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(buf))


def main():
    for artist, album, genre, year, tracks in ALBUMS:
        d = os.path.join(ROOT, artist, album)
        os.makedirs(d, exist_ok=True)
        for no, (title, freq) in enumerate(tracks, 1):
            p = os.path.join(d, "%02d %s.wav" % (no, title))
            tone(p, freq)
            f = WAVE(p)
            if f.tags is None:
                f.add_tags()
            for fr in (TIT2(encoding=3, text=title), TPE1(encoding=3, text=artist),
                       TPE2(encoding=3, text=artist), TALB(encoding=3, text=album),
                       TCON(encoding=3, text=genre), TDRC(encoding=3, text=year),
                       TRCK(encoding=3, text="%d/%d" % (no, len(tracks)))):
                f.tags.add(fr)
            f.save()
            print("wrote", p, os.path.getsize(p))


if __name__ == "__main__":
    main()
