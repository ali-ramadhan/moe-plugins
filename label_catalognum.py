"""Label and catalog number plugin for Moe.

Provides a label_catalognum() path-template function that renders an album's record
label and/or catalog number as a bracketed fragment — " {Warp WARPCD92}", " {Warp}",
or " {WARPCD92}" — and "" when the album has neither.

The fragment carries its own leading space and "{...}" braces, so it can sit in the
middle of a path component and vanish cleanly when empty. Drop the call into a path
template after another field:

    album_path = "{album.title} ({album.year}){label_catalognum(album)}"
"""

import moe


@moe.hookimpl
def create_path_template_func():
    return [label_catalognum]


def label_catalognum(album):
    if album.label and album.catalog_num:
        return f" {{{album.label} {album.catalog_num}}}"
    if album.label:
        return f" {{{album.label}}}"
    if album.catalog_num:
        return f" {{{album.catalog_num}}}"
    return ""
