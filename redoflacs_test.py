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
import re

import dynaconf
import moe
from moe import config
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
    """Register redoflacs_test hookspecs to Moe."""
    from redoflacs_test import Hooks
    pm.add_hookspecs(Hooks)


@moe.hookimpl
def add_config_validator(settings):
    """Add configuration validators for redoflacs_test plugin."""
    validators = [
        dynaconf.Validator("REDOFLACS_TEST.GLOBAL_JOBS", default=None, cast=int),
    ]
    settings.validators.register(*validators)


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


def _build_redoflacs_command(folder_path: Path) -> list[str]:
    """Build the redoflacs command with configured job options.

    Args:
        folder_path: Path to the folder containing FLAC files.

    Returns:
        List of command arguments for redoflacs.
    """
    cmd = ['redoflacs', '-B', '-t']  # test operation

    # Get configuration values
    try:
        global_jobs = config.CONFIG.settings.redoflacs_test.global_jobs
        if global_jobs is not None:
            cmd.append(f'-j{global_jobs}')
    except (AttributeError, KeyError):
        pass

    cmd.append(str(folder_path))
    return cmd


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
        # Build redoflacs command with configured job options
        cmd = _build_redoflacs_command(folder_path)

        # Run redoflacs to test all FLAC files in the folder
        # Don't capture output - let it show directly to the user
        result = subprocess.run(
            cmd,
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


def _get_album_root_folder(track_path: Path) -> Path:
    """Get the album root folder for a track, handling multi-disc albums.

    For single-disc albums, returns the parent folder.
    For multi-disc albums, returns the grandparent folder if the parent looks like a disc folder.

    Args:
        track_path: Path to the FLAC track file.

    Returns:
        Path to the album root folder.
    """
    parent = track_path.parent

    # Check if parent folder name suggests it's a disc folder
    parent_name = parent.name.lower()

    # More specific patterns that indicate actual disc folders
    # Look for patterns like "disc 1", "disc1", "disk 1", "disk1", "cd 1", "cd1", etc.
    disc_patterns = [
        r'\bdisc\s*\d+\b',  # "disc 1", "disc1", "disc 01", etc.
        r'\bdisk\s*\d+\b',  # "disk 1", "disk1", "disk 01", etc.
        r'\bcd\s*\d+\b',    # "cd 1", "cd1", "cd 01", etc.
        r'\bdisc\s*[ivx]+\b',  # "disc i", "disc ii", "disc iii", etc. (roman numerals)
        r'\bdisk\s*[ivx]+\b',  # "disk i", "disk ii", "disk iii", etc.
        r'\bcd\s*[ivx]+\b',    # "cd i", "cd ii", "cd iii", etc.
    ]

    # If parent folder name matches disc folder patterns, assume it's a disc folder
    # and the album root is the grandparent
    if any(re.search(pattern, parent_name) for pattern in disc_patterns):
        if parent.parent.exists() and parent.parent != parent:
            return parent.parent

    # Otherwise, treat parent as the album root
    return parent


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
        # Get the album root folder (handles multi-disc albums)
        album_folder = _get_album_root_folder(track_path)
        validate_flac_folder(album_folder)


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
        validate_flac_folder(item.path)
    elif isinstance(item, Track) and item.path.suffix.lower() == '.flac':
        album_folder = _get_album_root_folder(item.path)
        validate_flac_folder(album_folder)
