"""Media and encoding plugin for Moe.

Provides a media_encoding() path-template function that summarizes an album's
source media and audio encoding as one "pretty" string, for example:

    CD FLAC 16bit 44.1kHz
    Vinyl FLAC 24bit 192kHz
    US WEB FLAC+MP3 16bit 44.1+48kHz
    FLAC 16bit 44.1kHz
    WEB MP3 44.1kHz
    CD FLAC 16bit 44.1+88.2kHz

It combines the album's media (normalized, so "Digital Media" becomes "WEB"), an
optional country prefix, and the audio format, bit depth, and sample rate collected
across all tracks. When tracks differ on a field, the distinct values are joined
with "+" (e.g. "16+24bit"). The function returns bare text, so add any brackets in
the template:

    album_path = "{album.title} [{media_encoding(album)}]"
"""

import re

import moe


@moe.hookimpl
def create_path_template_func():
    return [media_encoding]


media_regex_subs = [
    (r"digital\s+media", "WEB"),
    (r"vinyl", "Vinyl"),
]


def standardized_album_media(album):
    if not album.media:
        return ""

    for pattern, replacement in media_regex_subs:
        if re.search(pattern, album.media, re.IGNORECASE):
            return replacement

    return album.media


def album_audio_format(album):
    formats = sorted({track.audio_format.upper() for track in album.tracks})
    return "+".join(formats)


def album_bit_depth(album):
    depths = sorted({track.bit_depth for track in album.tracks if track.bit_depth})
    if not depths:
        return ""
    return "+".join(str(depth) for depth in depths) + "bit"


def pretty_sample_rate(f_Hz):
    f_kHz = f_Hz / 1000
    if f_kHz.is_integer():
        return str(int(f_kHz))
    return str(f_kHz)


def album_sample_rate(album):
    rates = sorted({track.sample_rate for track in album.tracks if track.sample_rate})
    if not rates:
        return ""
    return "+".join(map(pretty_sample_rate, rates)) + "kHz"


def media_encoding(album):
    parts = [
        standardized_album_media(album),
        album_audio_format(album),
        album_bit_depth(album),
        album_sample_rate(album),
    ]
    encoding = " ".join(part for part in parts if part)
    if album.country:
        return f"{album.country} {encoding}"
    return encoding
