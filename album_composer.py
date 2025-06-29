"""Album composer plugin for Moe.

This plugin provides album_composer() and album_composer_sort() functions for use in path templates.
Since composer is a track-level field, these functions determine the "album composer"
by analyzing the composers of all tracks in an album.
"""

import logging
from collections import Counter
from typing import Callable

import moe
from moe.library import Album


@moe.hookimpl
def create_path_template_func() -> list[Callable]:
    return [album_composer, album_composer_sort]


def album_composer(album: Album) -> str:
    composers = [track.composer for track in album.tracks if track.composer]

    if not composers:
        return ""

    if all(composer == composers[0] for composer in composers):
        return composers[0]

    composer_counts = Counter(composers)
    most_common_composer = composer_counts.most_common(1)[0][0]

    print(f"Multiple composers found: {dict(composer_counts)}, using most common: {most_common_composer}")

    return most_common_composer


def album_composer_sort(album: Album) -> str:
    composers_sort = [track.composer_sort for track in album.tracks if track.composer_sort]

    if not composers_sort:
        return ""

    if all(composer_sort == composers_sort[0] for composer_sort in composers_sort):
        return composers_sort[0]

    composer_sort_counts = Counter(composers_sort)
    most_common_composer_sort = composer_sort_counts.most_common(1)[0][0]

    print(f"Multiple composer_sorts found: {dict(composer_sort_counts)}, using most common: {most_common_composer_sort}")

    return most_common_composer_sort
