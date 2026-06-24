"""Article-moving plugin for Moe.

Provides a the() path-template function that moves a leading article ("the", "a",
"an") to the end of a name so it sorts naturally — "The Beatles" becomes
"Beatles, The". The match is case-insensitive but preserves the article's original
case.

Enable the plugin, then call the() on any name field in a path template, often
wrapped by bucket() so the article doesn't drive the alphabetical folder:

    album_path = "{bucket(the(album.artist))}/{the(album.artist)}/{album.title}"
"""

import moe


@moe.hookimpl
def create_path_template_func():
    return [the]


articles = ["the", "a", "an"]


def the(name):
    if not name:
        return name
    words = name.split()
    if len(words) > 1 and words[0].lower() in articles:
        return f"{' '.join(words[1:])}, {words[0]}"
    return name
