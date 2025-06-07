"""
Test FLAC files for validity using the 'redoflacs -t' command.

This plugin validates FLAC files in folders before they are considered for addition to the library.
If any FLAC file in a folder fails the test, it will raise an error preventing files from being added.
redoflacs provides better performance and parallel processing capabilities by validating entire folders.
"""

import logging
import subprocess
from pathlib import Path
from typing import Any, Set

import moe
from moe.library import Album, Track
from rich.console import Console

__all__ = ["TestFlacError"]

console = Console()

# Keep track of already validated folders to avoid duplicate validation
_validated_folders: Set[Path] = set()


class TestFlacError(Exception):
    """Error when FLAC files in a folder fail validation."""


class Hooks:
    """Test FLAC plugin hook specifications."""

    @staticmethod
    @moe.hookspec
    def validate_flac_folder(folder_path: Path) -> None:
        """Validate all FLAC files in a folder.

        Args:
            folder_path: Path to the folder containing FLAC files to validate.

        Raises:
            TestFlacError: If any FLAC files in the folder are invalid.
        """


@moe.hookimpl
def add_hooks(pm):
    """Register test_flac hookspecs to Moe."""
    from test_flac import Hooks
    pm.add_hookspecs(Hooks)


def _folder_contains_flac(folder_path: Path) -> bool:
    """Check if a folder contains any FLAC files.

    Args:
        folder_path: Path to check for FLAC files.

    Returns:
        True if folder contains FLAC files, False otherwise.
    """
    if not folder_path.is_dir():
        return False

    for file_path in folder_path.rglob("*.flac"):
        return True
    return False


@moe.hookimpl
def validate_flac_folder(folder_path: Path) -> None:
    """Validate all FLAC files in a folder using the 'redoflacs -t' command.

    Args:
        folder_path: Path to the folder containing FLAC files to validate.

    Raises:
        TestFlacError: If any FLAC files in the folder are invalid or the redoflacs command fails.
    """
    # Skip if folder doesn't contain FLAC files
    if not _folder_contains_flac(folder_path):
        return

    # Skip if already validated
    if folder_path in _validated_folders:
        return

    console.print(
        f"[bold blue]Testing FLAC files in folder:[/bold blue] [not bold yellow]{folder_path}[/not bold yellow]"
    )

    try:
        # Run 'redoflacs -t' to test all FLAC files in the folder
        # Don't capture output - let it show directly to the user
        result = subprocess.run(
            ['redoflacs', '-t', str(folder_path)],
            timeout=300  # 5 minute timeout for folder validation
        )

        if result.returncode != 0:
            error_msg = f"FLAC validation failed for folder: {folder_path} (exit code: {result.returncode})"
            console.print(f"[bold red]{error_msg}[/bold red]")
            raise TestFlacError(error_msg)

        # Mark folder as validated
        _validated_folders.add(folder_path)
        console.print(
            f"[bold green]✓ FLAC validation passed for folder:[/bold green] [not bold yellow]{folder_path}[/not bold yellow]"
        )

    except subprocess.TimeoutExpired:
        error_msg = f"FLAC validation timed out for folder: {folder_path}"
        console.print(f"[bold red]{error_msg}[/bold red]")
        raise TestFlacError(error_msg)
    except FileNotFoundError:
        error_msg = "redoflacs command not found. Please install redoflacs to enable FLAC validation."
        console.print(f"[bold red]{error_msg}[/bold red]")
        raise TestFlacError(error_msg)
    except Exception as e:
        error_msg = f"Error validating FLAC files in folder {folder_path}: {e}"
        console.print(f"[bold red]{error_msg}[/bold red]")
        raise TestFlacError(error_msg)


@moe.hookimpl(tryfirst=True)
def read_custom_tags(
    track_path: Path, album_fields: dict[str, Any], track_fields: dict[str, Any]
) -> None:
    """Validate FLAC folder before reading track tags.

    This hook runs before the standard tag reading process to ensure all FLAC files
    in the folder are valid before attempting to process any of them.

    Args:
        track_path: Path of the track file being processed.
        album_fields: Dictionary of album fields (unused in this hook).
        track_fields: Dictionary of track fields (unused in this hook).

    Raises:
        TestFlacError: If any FLAC files in the folder are invalid.
    """
    # Only validate if this is a FLAC file
    if track_path.suffix.lower() == '.flac':
        # Validate the parent folder containing the FLAC file
        folder_path = track_path.parent
        validate_flac_folder(folder_path)


@moe.hookimpl
def pre_add(item):
    """Validate FLAC folders before adding items to the library.

    This provides validation for both Album and Track items containing FLAC files.

    Args:
        item: Library item being added (Album or Track).

    Raises:
        TestFlacError: If any FLAC files in the item's folder are invalid.
    """
    if isinstance(item, Album):
        # Validate the album folder
        console.print(
            f"[bold cyan]Pre-add FLAC validation for album folder:[/bold cyan] [not bold yellow]{item.path}[/not bold yellow]"
        )
        validate_flac_folder(item.path)
    elif isinstance(item, Track) and item.path.suffix.lower() == '.flac':
        # Validate the folder containing the FLAC track
        folder_path = item.path.parent
        console.print(
            f"[bold cyan]Pre-add FLAC validation for track folder:[/bold cyan] [not bold yellow]{folder_path}[/not bold yellow]"
        )
        validate_flac_folder(folder_path)
