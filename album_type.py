"""Album Type Classification Plugin for Moe.

This plugin allows users to interactively categorize albums into different types
(regular album, compilation, soundtrack, classical music) and automatically
organizes them into appropriate directory structures.
"""

import logging
from typing import Optional

import questionary

import moe
from moe.library import Album

__all__ = ["classify_album_type"]

log = logging.getLogger("moe.album_type")

# Album type constants
ALBUM_TYPE_REGULAR = "regular"
ALBUM_TYPE_COMPILATION = "compilation"
ALBUM_TYPE_SOUNDTRACK = "soundtrack"
ALBUM_TYPE_CLASSICAL = "classical"

# Path templates for different album types
PATH_TEMPLATES = {
    ALBUM_TYPE_REGULAR: None,  # Use default config
    ALBUM_TYPE_COMPILATION: "Compilations/{album.title} ({year_reissue(album)}){label_catalognum(album)} [{media_encoding(album)}]",
    ALBUM_TYPE_SOUNDTRACK: "Soundtracks/{album.title} ({year_reissue(album)}){label_catalognum(album)} [{media_encoding(album)}]",
    ALBUM_TYPE_CLASSICAL: "Classical/{album.artist}/{album.title} ({year_reissue(album)}){label_catalognum(album)} [{media_encoding(album)}]",
}


@moe.hookimpl
def override_album_path_config(album: Album) -> Optional[str]:
    """Override album path based on album type classification."""
    # Just use the stored classification - prompting happens in edit_new_items
    album_type = album.custom.get("album_type")
    return PATH_TEMPLATES.get(album_type)


def _auto_detect_album_type(album: Album) -> Optional[str]:
    """Attempt to automatically detect album type based on metadata."""
    title_lower = album.title.lower() if album.title else ""
    artist_lower = album.artist.lower() if album.artist else ""

    # Check for soundtrack indicators
    soundtrack_keywords = [
        "soundtrack", "score", "original motion picture", "ost", "theme from",
        "music from", "songs from", "film score", "movie soundtrack"
    ]
    if any(keyword in title_lower for keyword in soundtrack_keywords):
        return ALBUM_TYPE_SOUNDTRACK

    # Check for classical music indicators
    classical_keywords = [
        "symphony", "concerto", "sonata", "quartet", "quintet", "opera",
        "requiem", "mass", "cantata", "oratorio", "fugue", "prelude",
        "nocturne", "etude", "waltz", "polonaise", "mazurka"
    ]
    classical_composers = [
        "bach", "mozart", "beethoven", "chopin", "tchaikovsky", "vivaldi",
        "brahms", "schubert", "debussy", "stravinsky", "rachmaninoff",
        "handel", "haydn", "liszt", "wagner", "mahler", "dvorak"
    ]

    if (any(keyword in title_lower for keyword in classical_keywords) or
        any(composer in artist_lower for composer in classical_composers)):
        return ALBUM_TYPE_CLASSICAL

    # Check for compilation indicators
    compilation_keywords = [
        "compilation", "greatest hits", "best of", "collection", "anthology",
        "various artists", "va -", "mixed by", "selected by", "compiled by"
    ]
    if (any(keyword in title_lower for keyword in compilation_keywords) or
        any(keyword in artist_lower for keyword in compilation_keywords)):
        return ALBUM_TYPE_COMPILATION

    # If no clear indicators, return None (will prompt user if enabled)
    return None


def _prompt_for_album_type(album: Album, default_choice: Optional[str] = None) -> Optional[str]:
    """Prompt the user to classify the album type."""
    try:
        print(f"\n🎵 Album Classification Required")
        print(f"Artist: {album.artist}")
        print(f"Title: {album.title}")
        if album.date:
            print(f"Year: {album.date.year}")

        # Show auto-detection result if available
        if default_choice:
            type_labels = {
                ALBUM_TYPE_REGULAR: "regular album",
                ALBUM_TYPE_COMPILATION: "compilation",
                ALBUM_TYPE_SOUNDTRACK: "soundtrack",
                ALBUM_TYPE_CLASSICAL: "classical music"
            }
            suggested_label = type_labels.get(default_choice, "unknown")
            print(f"💡 Auto-detected as: {suggested_label}")

        print("-" * 50)

        # Create choices with icons and determine default index
        choices = [
            questionary.Choice("🎶 Regular Album", ALBUM_TYPE_REGULAR),
            questionary.Choice("📀 Compilation/Various Artists", ALBUM_TYPE_COMPILATION),
            questionary.Choice("🎬 Soundtrack/Score", ALBUM_TYPE_SOUNDTRACK),
            questionary.Choice("🎼 Classical Music", ALBUM_TYPE_CLASSICAL),
            questionary.Choice("❓ Skip classification", None),
        ]

        # Find the default choice index
        default_index = 0  # Default to regular album
        if default_choice:
            for i, choice in enumerate(choices):
                if choice.value == default_choice:
                    default_index = i
                    break

        choice = questionary.select(
            "How would you like to categorize this album?",
            choices=choices,
            default=choices[default_index]
        ).ask()

        if choice:
            type_labels = {
                ALBUM_TYPE_REGULAR: "regular album",
                ALBUM_TYPE_COMPILATION: "compilation",
                ALBUM_TYPE_SOUNDTRACK: "soundtrack",
                ALBUM_TYPE_CLASSICAL: "classical music"
            }
            print(f"✅ Classified as: {type_labels.get(choice, 'unknown')}")

        return choice

    except (KeyboardInterrupt, EOFError):
        print("\n⏭️  Skipping album classification.")
        return None
    except Exception as e:
        log.error(f"Error during album type prompting: {e}")
        return None


def classify_album_type(album: Album, album_type: str) -> None:
    """Manually classify an album type (for use by other plugins)."""
    if album_type not in PATH_TEMPLATES:
        raise ValueError(f"Invalid album type: {album_type}")

    album.custom["album_type"] = album_type
    log.info(f"Manually classified album as '{album_type}': {album.artist} - {album.title}")


@moe.hookimpl
def edit_new_items(session, items):
    """Classify new albums being added to the library."""
    albums = [item for item in items if isinstance(item, Album)]

    for album in albums:
        # Skip if already classified
        if "album_type" in album.custom:
            continue

        # Auto-detect to suggest a default
        suggested_type = _auto_detect_album_type(album)

        # Always prompt the user with auto-detected suggestion as default
        album_type = _prompt_for_album_type(album, default_choice=suggested_type)

        # Store the classification
        if album_type:
            album.custom["album_type"] = album_type
            log.info(f"Classified album as '{album_type}': {album.artist} - {album.title}")
        else:
            # If user skipped, default to regular
            album.custom["album_type"] = ALBUM_TYPE_REGULAR
            log.debug(f"Defaulted to regular album: {album.artist} - {album.title}")
