"""
Compress FLAC files after they have been moved using redoflacs.

This plugin compresses FLAC files in the library after they've been moved and organized.
It uses redoflacs for efficient batch compression and reports compression ratios.
Files are compressed in-place to maintain library organization.
"""

import logging
import subprocess
import shutil
from pathlib import Path
from typing import Set

import moe
from moe.library import Album, Track
from rich.console import Console
from sqlalchemy.orm import Session

__all__ = ["CompressFlacError"]

console = Console()

# Keep track of already compressed folders to avoid duplicate processing
_compressed_folders: Set[Path] = set()


class CompressFlacError(Exception):
    """Error when FLAC compression fails."""


class Hooks:
    """Compress FLAC plugin hook specifications."""

    @staticmethod
    @moe.hookspec
    def compress_flac_folder(folder_path: Path) -> None:
        """Compress all FLAC files in a folder.

        Args:
            folder_path: Path to the folder containing FLAC files to compress.

        Raises:
            CompressFlacError: If FLAC compression fails.
        """


@moe.hookimpl
def add_hooks(pm):
    """Register compress_flac hookspecs to Moe."""
    from redoflacs_compress import Hooks
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


def _get_file_size(file_path: Path) -> int:
    """Get file size in bytes.

    Args:
        file_path: Path to the file.

    Returns:
        File size in bytes, or 0 if file doesn't exist.
    """
    try:
        return file_path.stat().st_size
    except (OSError, FileNotFoundError):
        return 0


def _calculate_compression_ratio(original_size: int, compressed_size: int) -> float:
    """Calculate compression ratio as a percentage.

    Args:
        original_size: Original file size in bytes.
        compressed_size: Compressed file size in bytes.

    Returns:
        Compression ratio as percentage (e.g., 85.2 means 85.2% of original size).
    """
    if original_size == 0:
        return 100.0
    return (compressed_size / original_size) * 100.0


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format.

    Args:
        size_bytes: File size in bytes.

    Returns:
        Formatted file size string (e.g., "1.23 MiB", "456.78 KiB").
    """
    if size_bytes == 0:
        return "0.00 B"

    # Define units and their byte multipliers
    units = [
        ("GiB", 1024**3),
        ("MiB", 1024**2),
        ("KiB", 1024),
        ("B", 1)
    ]

    for unit_name, unit_size in units:
        if size_bytes >= unit_size:
            size_value = size_bytes / unit_size
            return f"{size_value:.2f} {unit_name}"

    return f"{size_bytes:.2f} B"


@moe.hookimpl
def compress_flac_folder(folder_path: Path) -> None:
    """Compress all FLAC files in a folder using redoflacs.

    Args:
        folder_path: Path to the folder containing FLAC files to compress.

    Raises:
        CompressFlacError: If FLAC compression fails.
    """
    # Skip if folder doesn't contain FLAC files
    if not _folder_contains_flac(folder_path):
        return

    # Skip if already compressed
    if folder_path in _compressed_folders:
        return

    console.print(
        f"[bold blue]Compressing FLAC files in folder:[/bold blue] [not bold yellow]{folder_path}[/not bold yellow]"
    )

    # Get original file sizes for compression ratio calculation
    flac_files = list(folder_path.rglob("*.flac"))
    original_sizes = {}
    total_original_size = 0

    for flac_file in flac_files:
        size = _get_file_size(flac_file)
        original_sizes[flac_file] = size
        total_original_size += size

    if total_original_size == 0:
        console.print("[yellow]⚠️  No FLAC files found or files are empty[/yellow]")
        return

    try:
        # Run 'redoflacs' to compress all FLAC files in the folder
        # This command compresses files in-place
        result = subprocess.run(
            ['redoflacs', str(folder_path)],
            timeout=600  # 10 minute timeout for folder compression
        )

        if result.returncode != 0:
            error_msg = f"FLAC compression failed for folder: {folder_path} (exit code: {result.returncode})"
            if result.stderr:
                error_msg += f"\nError: {result.stderr.strip()}"
            console.print(f"[bold red]{error_msg}[/bold red]")
            raise CompressFlacError(error_msg)

        # Calculate and report compression ratios
        total_compressed_size = 0
        individual_ratios = []

        for flac_file in flac_files:
            if flac_file.exists():
                compressed_size = _get_file_size(flac_file)
                original_size = original_sizes[flac_file]

                if original_size > 0:
                    ratio = _calculate_compression_ratio(original_size, compressed_size)
                    individual_ratios.append(ratio)
                    total_compressed_size += compressed_size

                    # Report individual file compression
                    savings = ((original_size - compressed_size) / original_size) * 100
                    console.print(
                        f"[green]✓[/green] {flac_file.name}: "
                        f"[blue]{_format_file_size(original_size)}[/blue] -> [blue]{_format_file_size(compressed_size)}[/blue] "
                        f"([bold green]{savings:.1f}% savings[/bold green])"
                    )

        # Report overall compression statistics
        if individual_ratios:
            overall_ratio = _calculate_compression_ratio(total_original_size, total_compressed_size)
            overall_savings = 100.0 - overall_ratio
            average_ratio = sum(individual_ratios) / len(individual_ratios)
            average_savings = 100.0 - average_ratio

            console.print(
                f"[bold green]🎉 Compression completed for {len(flac_files)} files![/bold green]\n"
                f"[bold cyan]Overall statistics:[/bold cyan]\n"
                f"  • Total size: [bold blue]{_format_file_size(total_original_size)}[/bold blue] -> [bold blue]{_format_file_size(total_compressed_size)}[/bold blue]\n"
                f"  • Overall savings: [bold green]{overall_savings:.1f}%[/bold green]\n"
                f"  • Average per-file savings: [bold green]{average_savings:.1f}%[/bold green]"
            )

        # Mark folder as compressed
        _compressed_folders.add(folder_path)

    except subprocess.TimeoutExpired:
        error_msg = f"FLAC compression timed out for folder: {folder_path}"
        console.print(f"[bold red]{error_msg}[/bold red]")
        raise CompressFlacError(error_msg)
    except FileNotFoundError:
        error_msg = "redoflacs command not found. Please install redoflacs to enable FLAC compression."
        console.print(f"[bold red]{error_msg}[/bold red]")
        raise CompressFlacError(error_msg)
    except Exception as e:
        error_msg = f"Error compressing FLAC files in folder {folder_path}: {e}"
        console.print(f"[bold red]{error_msg}[/bold red]")
        raise CompressFlacError(error_msg)


@moe.hookimpl
def process_new_items(session: Session, items):
    """Compress FLAC files after they have been moved and organized.

    This hook runs after files have been moved to their final locations in the library,
    ensuring compression happens on the organized files rather than the original imports.

    Args:
        session: Library database session.
        items: List of library items that were just added.
    """
    albums_to_compress = set()

    # Collect unique album folders that contain FLAC files
    for item in items:
        if isinstance(item, Album):
            # Check if album has any FLAC tracks
            for track in item.tracks:
                if track.path.suffix.lower() == '.flac':
                    albums_to_compress.add(item.path)
                    break
        elif isinstance(item, Track) and item.path.suffix.lower() == '.flac':
            # Individual track - compress its album folder
            albums_to_compress.add(item.path.parent)

    # Compress each unique album folder
    for album_path in albums_to_compress:
        try:
            compress_flac_folder(album_path)
        except CompressFlacError as e:
            # Log the error but don't stop processing other albums
            console.print(f"[bold red]Failed to compress {album_path}: {e}[/bold red]")
            logging.getLogger("moe.redoflacs_compress").error(f"Compression failed for {album_path}: {e}")
