"""Plugin to confirm album paths before files are moved.

This plugin prompts the user to confirm or edit the final album destination path
right before files are moved during the add process.
"""

import logging
from pathlib import Path

from prompt_toolkit import prompt
from prompt_toolkit.styles import Style
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
                [("class:prompt", "Confirm or edit album path (Ctrl+C to cancel): ")],
                default=current_path_str,
                validator=PathValidator(),
                validate_while_typing=False,
                style=Style.from_dict({
                    "": "bold #ffff00",  # Bold yellow styling for user input
                    "prompt": "#ffffff"  # Normal white styling for the prompt text
                })
            )

            new_path = new_path.strip()
            if new_path != current_path_str:
                log.info(f"Album path changed: '{current_path_str}' -> '{new_path}'")
                # Store the custom path for later retrieval by override_album_path_config
                album.custom['_confirm_album_path_override'] = new_path
                console.print("✅ Path updated to:", style="bold green", end=" ")
                console.print(new_path, style="bright_white")
            else:
                log.debug("Album path confirmed without changes")
                console.print("✅ Path confirmed", style="bold green")

        except (KeyboardInterrupt, EOFError):
            log.info("Path confirmation cancelled by user")
            console.print("\n🛑 Force exiting...", style="bold red")
            raise SystemExit(0)
        except Exception as e:
            log.error(f"Error during path confirmation: {e}")
            console.print(f"\n❌ Error: {e}", style="bold red")
            raise


@moe.hookimpl
def override_album_path_config(album):
    """Override the album path config with user-confirmed path."""
    custom_path = album.custom.get('_confirm_album_path_override')
    if custom_path:
        # Convert absolute path to a relative template
        library_path = Path(config.CONFIG.settings.library_path).expanduser()
        custom_path_obj = Path(custom_path)

        try:
            # If the custom path is absolute and under library_path, make it relative
            if custom_path_obj.is_absolute():
                relative_path = custom_path_obj.relative_to(library_path)
                # Escape braces to treat them as literal text, not f-string expressions
                escaped_path = str(relative_path).replace('{', '{{').replace('}', '}}')
                return escaped_path
            else:
                # If it's already relative, escape braces and use it as-is
                escaped_path = custom_path.replace('{', '{{').replace('}', '}}')
                return escaped_path
        except ValueError:
            # If the path is not under library_path, use it as an absolute template
            log.warning(f"Custom path is outside library path: {custom_path}")
            # Escape braces even for absolute paths
            escaped_path = custom_path.replace('{', '{{').replace('}', '}}')
            return escaped_path

    return None  # Use default config
