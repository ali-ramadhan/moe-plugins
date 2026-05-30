"""Bucket plugin for Moe.

Provides a bucket() path-template function that maps a name to a single-character
folder "bucket": an uppercased first letter (A-Z) for names starting with a letter,
"0-9" for names starting with a digit, and "#-!" for anything else.

Enable the plugin, then call bucket() on a name inside a path-template config value,
typically an artist or title wrapped in the() to ignore leading articles, for example:

    album_path = "Artists/{bucket(the(album.artist))}/{album.artist} - {album.title}"

Raises ValueError on an empty name rather than returning a fallback, so the wrapped
field must be present.
"""

import moe


@moe.hookimpl
def create_path_template_func():
    return [bucket]


def bucket(name):
    if not name:
        raise ValueError("Cannot bucket an empty name")
    first_char = name[0]
    if first_char.isalpha():
        return first_char.upper()
    if first_char.isnumeric():
        return "0-9"
    return "#-!"
