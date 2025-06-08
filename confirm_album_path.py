"""Plugin to confirm album paths before files are moved.

This plugin prompts the user to confirm or edit the final album destination path
right before files are moved during the add process.
"""

import logging
from pathlib import Path

from prompt_toolkit import prompt
from prompt_toolkit.validation import ValidationError, Validator
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

import moe
from moe import config
from moe.library import Album
from moe.move.move_core import fmt_item_path

log = logging.getLogger("confirm_album_path")
console = Console()


class PathValidator(Validator):
    """Validator for album paths."""

    def validate(self, document):
        """Validate the path."""
        path_str = document.text.strip()
        if not path_str:
            raise ValidationError(message="Path cannot be empty")

        try:
            Path(path_str)
        except (ValueError, OSError) as e:
            raise ValidationError(message=f"Invalid path: {e}")


@moe.hookimpl(trylast=True)
def edit_new_items(session, items):
    """Confirm album destination paths after album type classification."""
    albums = [item for item in items if isinstance(item, Album)]

    for album in albums:
        # Get the destination path that Moe will generate (after album type classification)
        try:
            destination_path = fmt_item_path(album)
        except Exception as e:
            log.error(f"Could not generate destination path: {e}")
            continue

        current_path_str = str(destination_path)
        source_path_str = str(album.path)

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

        # Album type if classified
        album_type = album.custom.get("album_type")
        if album_type:
            type_labels = {
                "regular": "🎶 Regular Album",
                "compilation": "📀 Compilation",
                "soundtrack": "🎬 Soundtrack",
                "classical": "🎼 Classical"
            }
            content.append("📂 Type: ", style="bold cyan")
            content.append(f"{type_labels.get(album_type, album_type)}\n", style="yellow")

        content.append("\n")

        # Source path
        content.append("📁 Source: ", style="bold green")
        content.append(f"{source_path_str}\n", style="dim")

        # Destination path
        content.append("🎯 Destination: ", style="bold magenta")
        content.append(f"{current_path_str}", style="bright_white")

        # Display the panel
        panel = Panel(
            content,
            title="[bold yellow]Album Path Confirmation[/bold yellow]",
            border_style="blue",
            padding=(1, 2)
        )

        console.print()
        console.print(panel)

        try:
            new_path = prompt(
                "Confirm or edit album path: ",
                default=current_path_str,
                validator=PathValidator(),
                validate_while_typing=False
            )

            new_path = new_path.strip()
            if new_path != current_path_str:
                log.info(f"Album path changed: '{current_path_str}' -> '{new_path}'")
                # Update the album's path to the new destination
                album.path = Path(new_path)
                console.print("✅ Path updated to:", style="bold green", end=" ")
                console.print(new_path, style="bright_white")
            else:
                log.debug("Album path confirmed without changes")
                console.print("✅ Path confirmed", style="bold green")

        except KeyboardInterrupt:
            log.info("Path confirmation cancelled by user")
            console.print("\n❌ Path confirmation cancelled", style="bold red")
            raise
        except Exception as e:
            log.error(f"Error during path confirmation: {e}")
            console.print(f"\n❌ Error: {e}", style="bold red")
            raise
