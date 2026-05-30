"""Album composer plugin for Moe.

Provides album_composer() and album_composer_sort() path-template functions.
Composer is a track-level field, so the "album composer" is derived as the most
common composer across an album's tracks (ties broken by first-seen track).

Enable the plugin, then call either function on the in-scope `album` inside a
path-template config value, for example:

    album_path = "{album_composer(album)}/{album.title} ({album.year})"

Each returns "" when no track has the field set, which drops that path component.
"""

from collections import Counter
from collections.abc import Callable
import logging

import moe
from moe.library import Album

log = logging.getLogger("moe.plugin.album_composer")


@moe.hookimpl
def create_path_template_func() -> list[Callable]:
    """Adds custom functions for the path templates."""
    return [album_composer, album_composer_sort]


def _most_common(album: Album, field: str) -> str:
    """Returns the most common non-empty value of a track ``field`` in an album."""
    values = [value for track in album.tracks if (value := getattr(track, field))]
    if not values:
        return ""

    counts = Counter(values)
    best, _ = counts.most_common(1)[0]
    if len(counts) > 1:
        log.warning(
            f"Album {album} has multiple {field}s {dict(counts)}; using {best!r}."
        )

    return best


def album_composer(album: Album) -> str:
    """Returns the album's composer."""
    return _most_common(album, "composer")


def album_composer_sort(album: Album) -> str:
    """Returns the album's composer sort name."""
    return _most_common(album, "composer_sort")
