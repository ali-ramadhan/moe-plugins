"""Disc and track numbering plugin for Moe.

Provides two path-template functions for multi-disc albums, both keyed off the
album's disc count:

- disc_dir_padded(track, album): a per-disc directory such as "Disc 1", or "" for a
  single-disc album so no extra directory is created.
- disc_and_track_num(track, album): the file's leading number, such as "02" for a
  single-disc album or "1-02" for a multi-disc one.

The disc number is zero-padded to the width of the album's disc count (e.g. "Disc 02"
and "03-05" for a 10-99 disc set) so names sort correctly. Enable the plugin and use
them in a track-path config value, before the title:

    track_path = "{disc_dir_padded(track, album)}/{disc_and_track_num(track, album)}"
"""

import moe


@moe.hookimpl
def create_path_template_func():
    return [disc_dir_padded, disc_and_track_num]


def _disc_width(album):
    """Digits to pad disc numbers to — the width of the album's disc count."""
    return len(str(album.disc_total))


def disc_dir_padded(track, album):
    """Per-disc subdirectory ("Disc 02"), or "" for a single-disc album."""
    if album.disc_total == 1:
        return ""
    return f"Disc {track.disc:0{_disc_width(album)}d}"


def disc_and_track_num(track, album):
    """Filename leading number: "TT" (single disc) or "D-TT" (multi-disc)."""
    if album.disc_total == 1:
        return f"{track.track_num:02d}"
    return f"{track.disc:0{_disc_width(album)}d}-{track.track_num:02d}"
