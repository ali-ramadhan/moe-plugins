"""
RateYourMusic Genre Plugin for Moe.

This plugin provides an interface to set genres for albums with integration
to open RateYourMusic pages for research. It hooks into the import/tagging workflow.
"""

import logging
import re
import webbrowser
from typing import Set

from prompt_toolkit import prompt
from prompt_toolkit.styles import Style
from prompt_toolkit.validation import Validator, ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from sqlalchemy.orm.session import Session

import moe
from moe.library import Album

__all__ = []

log = logging.getLogger("moe.rateyourmusic")
console = Console()


class GenreValidator(Validator):
    """Validator for genre input."""

    def validate(self, document):
        """Validate genre format."""
        text = document.text.strip()
        if not text:
            return  # Allow empty genres

        # Just basic validation - genres should be reasonable strings
        # Split by semicolon and check each genre
        genres = [g.strip() for g in text.split(';') if g.strip()]
        for genre in genres:
            if len(genre) > 100:  # Reasonable length limit
                raise ValidationError(message=f"Genre '{genre}' is too long (max 100 characters)")


def open_rateyourmusic(album: Album) -> bool:
    """Open RateYourMusic page for the album."""
    # Normalize artist and album for URL
    artist = album.artist.lower()
    title = album.title.lower()

    # Clean up for URL: remove special characters, replace spaces with hyphens
    artist_clean = re.sub(r'[^\w\s-]', '', artist).strip()
    artist_clean = re.sub(r'[-\s]+', '-', artist_clean)

    title_clean = re.sub(r'[^\w\s-]', '', title).strip()
    title_clean = re.sub(r'[-\s]+', '-', title_clean)

    # Create RYM URL
    rym_url = f"https://rateyourmusic.com/release/album/{artist_clean}/{title_clean}/"

    try:
        webbrowser.open(rym_url)
        log.info(f"Opened RateYourMusic page: {rym_url}")
        return True
    except Exception as e:
        log.error(f"Failed to open RateYourMusic page: {e}")
        return False


def get_album_genres(album: Album) -> Set[str]:
    """Get current genres from all tracks in the album."""
    all_genres = set()

    # Only get genres from tracks (albums don't have a genres field)
    for track in album.tracks:
        if track.genres:
            all_genres.update(track.genres)

    return all_genres


def set_album_genres(album: Album, genres: Set[str]):
    """Set genres for all tracks in the album."""
    # Only set genres on tracks (albums don't have a genres field)
    for track in album.tracks:
        track.genres = genres.copy()


def create_genre_editor_interface(album: Album) -> str:
    """Create an inline genre editor interface."""
    # Get current genres
    current_genres = get_album_genres(album)
    current_genres_str = "; ".join(sorted(current_genres)) if current_genres else ""

    # Create the formatted panel content
    content = Text()

    # Album title and artist
    content.append("🎵 Album: ", style="bold cyan")
    content.append(f"{album.artist} - {album.title}\n", style="white")

    # Date
    if album.date:
        content.append("📅 Date: ", style="bold cyan")
        content.append(f"{album.date}\n", style="white")

    # Label
    if album.label:
        content.append("🏷️  Label: ", style="bold cyan")
        content.append(f"{album.label}\n", style="white")

    # Track count
    content.append("🎶 Tracks: ", style="bold cyan")
    content.append(f"{len(album.tracks)}\n", style="white")

    content.append("\n")

    # Current genres
    content.append("🎭 Current Genres: ", style="bold magenta")
    if current_genres:
        content.append(f"{current_genres_str}", style="bright_white")
    else:
        content.append("None", style="dim")

    # Display the panel
    panel = Panel(
        content,
        title="[bold yellow]RateYourMusic Genre Editor[/bold yellow]",
        border_style="blue",
        padding=(1, 2)
    )

    console.print()
    console.print(panel)

    # Offer to open RateYourMusic page first
    try:
        open_rym = prompt(
            [("class:prompt", "Open RateYourMusic page for research? (y/N): ")],
            style=Style.from_dict({
                "": "#ffff00",
                "prompt": "#ffffff"
            })
        )

        if open_rym.lower().startswith('y'):
            if open_rateyourmusic(album):
                console.print("✅ RateYourMusic page opened in browser", style="bold green")
            else:
                console.print("❌ Failed to open RateYourMusic page", style="bold red")
            console.print()

    except (KeyboardInterrupt, EOFError):
        console.print("\n🛑 Force exiting...", style="bold red")
        raise SystemExit(0)

    # Genre editing prompt
    try:
        console.print("💡 Enter genres separated by semicolons (e.g., 'Rock; Alternative Rock; Indie')")
        console.print("   Leave empty to skip genre editing, or enter new genres to replace current ones")
        console.print()

        new_genres_str = prompt(
            [("class:prompt", "Genres: ")],
            default=current_genres_str,
            validator=GenreValidator(),
            validate_while_typing=False,
            style=Style.from_dict({
                "": "bold #ffff00",
                "prompt": "#ffffff"
            })
        )

        new_genres_str = new_genres_str.strip()

        # Parse genres
        if new_genres_str:
            new_genres = {g.strip() for g in new_genres_str.split(';') if g.strip()}
        else:
            new_genres = set()

        # Check if genres changed
        if new_genres != current_genres:
            set_album_genres(album, new_genres)
            if new_genres:
                console.print("✅ Genres updated to:", style="bold green", end=" ")
                console.print("; ".join(sorted(new_genres)), style="bright_white")
            else:
                console.print("✅ All genres cleared", style="bold green")
            return "continue"
        else:
            console.print("✅ Genres unchanged", style="bold green")
            return "skip"

    except (KeyboardInterrupt, EOFError):
        console.print("\n🛑 Force exiting...", style="bold red")
        raise SystemExit(0)
    except Exception as e:
        log.error(f"Error during genre editing: {e}")
        console.print(f"\n❌ Error: {e}", style="bold red")
        return "quit"


# Moe Integration Hooks
@moe.hookimpl
def edit_new_items(session: Session, items):
    """Edit new items during import - prompt for genre setting."""
    # Group albums to process
    albums = [item for item in items if isinstance(item, Album)]

    if not albums:
        return

    for album in albums:
        try:
            result = create_genre_editor_interface(album)

            if result == "continue":
                log.info(f"Genre editing completed for: {album.artist} - {album.title}")
            elif result == "skip":
                log.info(f"Genre editing skipped for: {album.artist} - {album.title}")
            elif result == "quit":
                log.info(f"Genre editing cancelled for: {album.artist} - {album.title}")
                # Continue with import process even if cancelled
            else:
                log.warning(f"Unexpected result from genre editor: {result}")

        except Exception as e:
            log.error(f"Error in RateYourMusic genre editor: {e}")
            import traceback
            log.error(f"Traceback: {traceback.format_exc()}")
            console.print(f"❌ Error in genre editor: {e}", style="bold red")
            # Continue with import process even if there's an error
