import re
import moe
from rich.console import Console

from pathlib import Path

console = Console()

@moe.hookimpl
def create_path_template_func():
    return [organize_extras]

def _organize_extras(extra):
    album = extra.album

    # Define patterns
    cover_pattern = re.compile(r"^.*(cover|folder).*\.(jpg|jpeg|png)$", re.IGNORECASE)
    cue_log_pattern = re.compile(r"^.*\.(cue|log)$", re.IGNORECASE)
    artwork_folder_pattern = re.compile(r"^(scan|scans|artwork)$", re.IGNORECASE)

    if cover_pattern.match(extra.path.name):
        ext = extra.path.suffix
        return f"{album.title}{ext}"

    elif any(artwork_folder_pattern.match(part) for part in extra.path.parts):
        # Preserve the folder structure from the matching folder onwards
        artwork_index = next(i for i, part in enumerate(extra.path.parts) if artwork_folder_pattern.match(part))
        preserved_path = Path(*extra.path.parts[artwork_index:])
        return str(preserved_path)

    elif cue_log_pattern.match(extra.path.name):
        ext = extra.path.suffix

        if album.disc_total == 1:
            return f"{album.title}{ext}"
        else:
            # Try to determine the disc number from the file path
            disc_match = re.search(r"disc\s*(\d+)", str(extra.path).lower())
            if disc_match:
                disc_num = disc_match.group(1)
            else:
                # If we can't determine the disc number, use a placeholder
                disc_num = "X"

            disc_dir = f"Disc {disc_num}"
            return str(Path(disc_dir) / f"{album.title} - Disc {disc_num}{ext}")

    else:
        return extra.path.name

def organize_extras(extra):
    new_path = _organize_extras(extra)
    console.print(
        f"[bold cyan]Determined extra path:[/bold cyan] [not bold yellow]{extra.path}[/not bold yellow] [bold cyan]->[/bold cyan] [not bold green]{new_path}[/not bold green]"
    )
    return new_path
