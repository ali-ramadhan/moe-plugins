import argparse
import io
import json
import shlex
import subprocess
import tempfile
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import dynaconf
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from PIL import Image
from sqlalchemy.orm.session import Session
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.application import get_app
from prompt_toolkit.styles import Style
from prompt_toolkit.shortcuts import prompt

import moe
from moe import config
from moe.library import Album, Extra


# =============================================================================
# CORE DATA CLASSES AND UTILITIES
# =============================================================================

@dataclass
class AlbumArtInfo:
    """Data class to hold album art information."""
    has_art: bool
    image_data: Optional[bytes] = None
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    size_bytes: Optional[int] = None
    error: Optional[str] = None


class FormatUtils:
    """Centralized formatting utilities."""

    @staticmethod
    def format_file_size(size_bytes: int) -> str:
        """Format file size in human-readable units."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KiB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MiB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GiB"

    @staticmethod
    def print_album_art_info(file_name: str, art_info: AlbumArtInfo):
        """Print album art information in a consistent format."""
        if not art_info.has_art:
            if art_info.error:
                print(f"❌ {file_name}: Error analyzing album art - {art_info.error}")
            else:
                print(f"🚫 {file_name}: No embedded album art found")
            return

        # Build details string
        details = []

        if art_info.width and art_info.height and art_info.format:
            details.append(f"{art_info.width}×{art_info.height}")
            details.append(art_info.format)

            # Add aspect ratio if not square
            aspect_ratio = art_info.width / art_info.height
            if not (0.99 <= aspect_ratio <= 1.01):  # Not approximately square
                details.append(f"{aspect_ratio:.3f}:1")

        if art_info.size_bytes:
            size_str = FormatUtils.format_file_size(art_info.size_bytes)
            details.append(size_str)

        # Create the single line output
        details_str = " | ".join(details) if details else "details unavailable"

        if art_info.error:
            print(f"🎨 {file_name}: Album art found ({details_str}) - Warning: {art_info.error}")
        else:
            print(f"🎨 {file_name}: Album art found ({details_str})")


class ErrorHandler:
    """Centralized error handling and logging."""

    @staticmethod
    def handle_file_error(operation: str, file_path: Path, error: Exception):
        """Handle file-related errors with consistent messaging."""
        print(f"❌ Error {operation} file {file_path.name}: {error}")

    @staticmethod
    def handle_image_error(operation: str, error: Exception):
        """Handle image processing errors with consistent messaging."""
        print(f"❌ Error {operation} image: {error}")

    @staticmethod
    def handle_audio_error(operation: str, error: Exception):
        """Handle audio processing errors with consistent messaging."""
        print(f"❌ Error {operation} audio: {error}")

    @staticmethod
    def handle_network_error(operation: str, error: Exception):
        """Handle network-related errors with consistent messaging."""
        print(f"❌ Network error during {operation}: {error}")


class PathUtils:
    """Centralized path handling utilities."""

    @staticmethod
    def get_temp_art_dir() -> Path:
        """Get the temporary directory for album art files."""
        temp_dir = Path(tempfile.gettempdir()) / "moe_album_art"
        temp_dir.mkdir(exist_ok=True)
        return temp_dir

    @staticmethod
    def validate_image_extensions(file_path: Path) -> bool:
        """Check if file has a valid image extension."""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp'}
        return file_path.suffix.lower() in image_extensions

    @staticmethod
    def scan_for_images(directory: Path) -> List[Path]:
        """Scan a directory for image files."""
        try:
            image_files = []
            for file_path in directory.iterdir():
                if file_path.is_file() and PathUtils.validate_image_extensions(file_path):
                    image_files.append(file_path)
            return sorted(image_files, key=lambda x: x.stat().st_size, reverse=True)
        except Exception as e:
            print(f"❌ Error scanning directory {directory}: {e}")
            return []

    @staticmethod
    def validate_local_image_file(file_path_str: str) -> Optional[Path]:
        """Validate that a local file path exists and is a valid image file."""
        try:
            file_path = Path(file_path_str).expanduser().resolve()

            if not file_path.exists():
                print(f"❌ File does not exist: {file_path}")
                return None

            if not file_path.is_file():
                print(f"❌ Path is not a file: {file_path}")
                return None

            if not PathUtils.validate_image_extensions(file_path):
                print(f"❌ File does not appear to be an image: {file_path}")
                return None

            # Try to open it as an image to validate
            try:
                with Image.open(file_path):
                    print(f"✅ Valid image file: {file_path.name}")
                    # Display info about the image
                    art_info = ImageProcessor.analyze_image_file(file_path)
                    FormatUtils.print_album_art_info(file_path.name, art_info)
            except Exception as e:
                print(f"❌ Cannot open as image: {e}")
                return None

            return file_path

        except Exception as e:
            print(f"❌ Error validating file path: {e}")
            return None


class ImageProcessor:
    """Centralized handler for image operations."""

    @staticmethod
    def analyze_image_data(image_data: bytes, has_error: bool = False, error_msg: str = None) -> AlbumArtInfo:
        """Create AlbumArtInfo from image data with consistent error handling."""
        try:
            image = Image.open(io.BytesIO(image_data))
            return AlbumArtInfo(
                has_art=True,
                image_data=image_data,
                width=image.size[0],
                height=image.size[1],
                format=image.format,
                size_bytes=len(image_data),
                error=error_msg if has_error else None
            )
        except Exception as e:
            return AlbumArtInfo(
                has_art=True,
                image_data=image_data,
                size_bytes=len(image_data),
                error=str(e)
            )

    @staticmethod
    def analyze_image_file(file_path: Path) -> AlbumArtInfo:
        """Create AlbumArtInfo from image file with consistent error handling."""
        try:
            file_size = file_path.stat().st_size

            try:
                with Image.open(file_path) as image:
                    return AlbumArtInfo(
                        has_art=True,
                        width=image.size[0],
                        height=image.size[1],
                        format=image.format,
                        size_bytes=file_size
                    )
            except Exception as img_error:
                return AlbumArtInfo(
                    has_art=True,
                    size_bytes=file_size,
                    error=str(img_error)
                )
        except Exception as file_error:
            return AlbumArtInfo(
                has_art=False,
                error=f"Error reading file - {file_error}"
            )

    @staticmethod
    def get_mime_type(image_path: Path) -> str:
        """Get MIME type for an image file."""
        suffix = image_path.suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.tiff': 'image/tiff',
            '.tif': 'image/tiff',
            '.webp': 'image/webp'
        }
        return mime_types.get(suffix, 'image/jpeg')

    @staticmethod
    def compress_jpeg_image(image_path: Path, quality: int = 90, output_dir: Optional[Path] = None) -> Optional[Path]:
        """Compress a JPEG image using ImageMagick with specified quality. Returns path to new compressed file."""
        # Check if ImageMagick is available
        try:
            subprocess.run(["convert", "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"\n❌ ImageMagick 'convert' command not found. Please install ImageMagick to compress images.")
            return None

        # Get original file size
        original_size = image_path.stat().st_size

        # Create compressed version with quality suffix in filename
        stem = image_path.stem
        suffix = image_path.suffix
        compressed_filename = f"{stem}_q{quality}{suffix}"

        # Determine output directory - use temp directory for local images to avoid modifying originals
        if output_dir is None:
            temp_dir = PathUtils.get_temp_art_dir()
            if not str(image_path).startswith(str(temp_dir)):
                # It's a local image, save to temp directory
                output_dir = temp_dir
            else:
                # It's already a temp file, save alongside it
                output_dir = image_path.parent

        compressed_path = output_dir / compressed_filename

        try:
            # Use ImageMagick to compress the JPEG to specified quality
            subprocess.run([
                "convert",
                str(image_path),
                "-quality", str(quality),
                str(compressed_path)
            ], check=True, capture_output=True)

            # Get compressed file size
            compressed_size = compressed_path.stat().st_size

            # Calculate compression ratio
            if compressed_size < original_size:
                ratio = (original_size - compressed_size) / original_size * 100
                print(f"\n✅ Created compressed version: {compressed_filename}")
                print(f"   {FormatUtils.format_file_size(original_size)} → {FormatUtils.format_file_size(compressed_size)} (-{ratio:.1f}%)")
                return compressed_path
            else:
                # Remove compressed version if it's not smaller
                compressed_path.unlink()
                print(f"\n⚠️  Compression didn't reduce file size for {image_path.name} at quality {quality}")
                return None

        except subprocess.CalledProcessError as e:
            print(f"\n❌ Error compressing image: {e}")
            # Clean up if compression failed
            if compressed_path.exists():
                compressed_path.unlink()
            return None
        except Exception as e:
            print(f"\n❌ Unexpected error during compression: {e}")
            # Clean up if compression failed
            if compressed_path.exists():
                compressed_path.unlink()
            return None

    @staticmethod
    def resize_image(image_path: Path, size_str: str, output_dir: Optional[Path] = None) -> Optional[Path]:
        """Resize an image using ImageMagick with specified dimensions. Returns path to new resized file."""
        # Check if ImageMagick is available
        try:
            subprocess.run(["convert", "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"\n❌ ImageMagick 'convert' command not found. Please install ImageMagick to resize images.")
            return None

        # Validate size format (should be like "1750x1750" or "1750x1750!")
        import re
        size_pattern = r'^(\d+)x(\d+)(!?)$'
        match = re.match(size_pattern, size_str)
        if not match:
            print(f"\n❌ Invalid size format: '{size_str}'. Use format like '1750x1750' or '1750x1750!' (with ! to ignore aspect ratio)")
            return None

        width, height, force_flag = match.groups()

        # Get original file info
        try:
            with Image.open(image_path) as img:
                original_width, original_height = img.size
        except Exception as e:
            print(f"\n❌ Error reading original image: {e}")
            return None

        # Create resized version with size suffix in filename
        stem = image_path.stem
        suffix = image_path.suffix
        size_suffix = f"{width}x{height}" + ("!" if force_flag else "")
        resized_filename = f"{stem}_{size_suffix}{suffix}"

        # Determine output directory - use temp directory for local images to avoid modifying originals
        if output_dir is None:
            temp_dir = PathUtils.get_temp_art_dir()
            if not str(image_path).startswith(str(temp_dir)):
                # It's a local image, save to temp directory
                output_dir = temp_dir
            else:
                # It's already a temp file, save alongside it
                output_dir = image_path.parent

        resized_path = output_dir / resized_filename

        try:
            # Use ImageMagick to resize the image
            resize_arg = f"{width}x{height}" + ("!" if force_flag else "")
            subprocess.run([
                "convert",
                str(image_path),
                "-resize", resize_arg,
                str(resized_path)
            ], check=True, capture_output=True)

            # Get resized image info
            try:
                with Image.open(resized_path) as img:
                    new_width, new_height = img.size
                resized_size = resized_path.stat().st_size
                original_size = image_path.stat().st_size

                print(f"\n✅ Created resized version: {resized_filename}")
                print(f"   {original_width}×{original_height} → {new_width}×{new_height}")
                print(f"   {FormatUtils.format_file_size(original_size)} → {FormatUtils.format_file_size(resized_size)}")
                return resized_path
            except Exception as e:
                print(f"\n✅ Created resized version: {resized_filename}")
                print(f"   Requested size: {width}×{height}")
                return resized_path

        except subprocess.CalledProcessError as e:
            print(f"\n❌ Error resizing image: {e}")
            # Clean up if resize failed
            if resized_path.exists():
                resized_path.unlink()
            return None
        except Exception as e:
            print(f"\n❌ Unexpected error during resize: {e}")
            # Clean up if resize failed
            if resized_path.exists():
                resized_path.unlink()
            return None


class AudioFileHandler:
    """Centralized handler for audio file operations."""

    @staticmethod
    def get_format(file_path: Path) -> str:
        """Get the audio file format from file extension."""
        return file_path.suffix.lower()

    @staticmethod
    def analyze_embedded_art(audio_file_path: Path) -> AlbumArtInfo:
        """Analyze album art in an audio file and return structured information."""
        try:
            suffix = AudioFileHandler.get_format(audio_file_path)
            album_art_data = None

            if suffix == '.flac':
                album_art_data = AudioFileHandler._extract_flac_art(audio_file_path)
            elif suffix == '.mp3':
                album_art_data = AudioFileHandler._extract_mp3_art(audio_file_path)
            else:
                return AlbumArtInfo(has_art=False, error=f"Unsupported format: {suffix}")

            if album_art_data:
                return ImageProcessor.analyze_image_data(album_art_data)
            else:
                return AlbumArtInfo(has_art=False)

        except Exception as e:
            return AlbumArtInfo(has_art=False, error=str(e))

    @staticmethod
    def _extract_flac_art(audio_file_path: Path) -> Optional[bytes]:
        """Extract album art data from FLAC file."""
        flac_file = FLAC(audio_file_path)
        if flac_file.pictures:
            return flac_file.pictures[0].data
        return None

    @staticmethod
    def _extract_mp3_art(audio_file_path: Path) -> Optional[bytes]:
        """Extract album art data from MP3 file."""
        try:
            id3_file = ID3(audio_file_path)
            for key in id3_file.keys():
                if key.startswith('APIC:'):
                    return id3_file[key].data
        except ID3NoHeaderError:
            pass
        return None

    @staticmethod
    def embed_art(audio_file_path: Path, image_path: Path) -> bool:
        """Embed album art into an audio file."""
        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()

            mime_type = ImageProcessor.get_mime_type(image_path)
            suffix = AudioFileHandler.get_format(audio_file_path)

            if suffix == '.flac':
                return AudioFileHandler._embed_flac_art(audio_file_path, image_data, mime_type)
            elif suffix == '.mp3':
                return AudioFileHandler._embed_mp3_art(audio_file_path, image_data, mime_type)
            else:
                print(f"⚠️  Unsupported audio format: {suffix}")
                return False

        except Exception as e:
            print(f"❌ Error embedding art: {e}")
            return False

    @staticmethod
    def _embed_flac_art(audio_file_path: Path, image_data: bytes, mime_type: str) -> bool:
        """Embed album art in FLAC file."""
        try:
            flac_file = FLAC(audio_file_path)
            flac_file.clear_pictures()

            picture = Picture()
            picture.type = 3  # Cover (front)
            picture.mime = mime_type
            picture.desc = "Cover"
            picture.data = image_data

            flac_file.add_picture(picture)
            flac_file.save()
            return True
        except Exception:
            return False

    @staticmethod
    def _embed_mp3_art(audio_file_path: Path, image_data: bytes, mime_type: str) -> bool:
        """Embed album art in ID3 tags (MP3)."""
        try:
            try:
                id3_file = ID3(audio_file_path)
            except ID3NoHeaderError:
                id3_file = ID3()

            # Remove existing album art
            for key in list(id3_file.keys()):
                if key.startswith('APIC:'):
                    del id3_file[key]

            id3_file.add(
                APIC(
                    encoding=3,  # UTF-8
                    mime=mime_type,
                    type=3,  # Cover (front)
                    desc="Cover",
                    data=image_data
                )
            )
            id3_file.save(audio_file_path)
            return True
        except Exception:
            return False

    @staticmethod
    def remove_embedded_art(audio_file_path: Path) -> bool:
        """Remove embedded album art from an audio file."""
        try:
            suffix = AudioFileHandler.get_format(audio_file_path)

            if suffix == '.flac':
                return AudioFileHandler._remove_flac_art(audio_file_path)
            elif suffix == '.mp3':
                return AudioFileHandler._remove_mp3_art(audio_file_path)
            else:
                return False

        except Exception:
            return False

    @staticmethod
    def _remove_flac_art(audio_file_path: Path) -> bool:
        """Remove album art from FLAC file."""
        try:
            flac_file = FLAC(audio_file_path)
            if flac_file.pictures:
                flac_file.clear_pictures()
                flac_file.save()
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def _remove_mp3_art(audio_file_path: Path) -> bool:
        """Remove album art from ID3 tags (MP3)."""
        try:
            try:
                id3_file = ID3(audio_file_path)
            except ID3NoHeaderError:
                return False

            # Check if there are any APIC frames to remove
            apic_keys = [key for key in id3_file.keys() if key.startswith('APIC:')]
            if apic_keys:
                # Remove existing album art
                for key in apic_keys:
                    del id3_file[key]
                id3_file.save(audio_file_path)
                return True
            return False
        except Exception:
            return False


class AlbumArtManager:
    """High-level manager for album art operations."""

    def __init__(self):
        self.audio_handler = AudioFileHandler()
        self.image_processor = ImageProcessor()
        self.path_utils = PathUtils()

    def analyze_directory(self, directory: Path, recursive: bool = False) -> dict:
        """Analyze album art in all audio files in a directory."""
        audio_extensions = {'.mp3', '.flac'}

        audio_files = []
        if recursive:
            for ext in audio_extensions:
                audio_files.extend(directory.rglob(f"*{ext}"))
        else:
            for ext in audio_extensions:
                audio_files.extend(directory.glob(f"*{ext}"))

        if not audio_files:
            return {'total_files': 0, 'files_with_art': 0, 'files_without_art': 0}

        files_with_art = 0
        files_without_art = 0

        for audio_file in audio_files:
            art_info = self.audio_handler.analyze_embedded_art(audio_file)
            if art_info.has_art:
                files_with_art += 1
            else:
                files_without_art += 1

        return {
            'total_files': len(audio_files),
            'files_with_art': files_with_art,
            'files_without_art': files_without_art,
            'audio_files': audio_files
        }

    def embed_art_interactive(self, tracks: List, image_files: List[Path]):
        """Manage interactive album art embedding for tracks."""
        tracks_with_art = []
        tracks_without_art = []

        for track in tracks:
            art_info = self.audio_handler.analyze_embedded_art(track.path)
            if art_info.has_art:
                tracks_with_art.append(track)
            else:
                tracks_without_art.append(track)

        # Determine target tracks based on current state
        if tracks_with_art and not tracks_without_art:
            from . import create_confirm_dialog  # Local import to avoid circular dependency
            choice = create_confirm_dialog(
                f"All {len(tracks)} tracks already have embedded album art. Replace it?"
            )
            if not choice:
                return None
            target_tracks = tracks
        elif tracks_with_art:
            from . import create_select_dialog  # Local import to avoid circular dependency
            choice = create_select_dialog(
                f"{len(tracks_with_art)} tracks have art, {len(tracks_without_art)} don't. What to do?",
                choices=[
                    "Embed in tracks without art only",
                    "Replace art in all tracks",
                    "Skip embedding"
                ]
            )

            if choice == "Skip embedding":
                return None
            elif choice == "Embed in tracks without art only":
                target_tracks = tracks_without_art
            else:
                target_tracks = tracks
        else:
            target_tracks = tracks

        return target_tracks

    def process_album_import(self, album: Album) -> Optional[Path]:
        """Process album art during the import workflow."""
        # Get source directory from album path
        source_dir = album.path

        # Analyze existing embedded art in all tracks
        if album.tracks:
            sorted_tracks = sorted(album.tracks, key=lambda t: (t.disc, t.track_num))
            for track in sorted_tracks:
                art_info = self.audio_handler.analyze_embedded_art(track.path)
                FormatUtils.print_album_art_info(track.path.name, art_info)

        # Look for image files in the source directory
        image_files = self.path_utils.scan_for_images(source_dir)

        # Always present the interactive selection interface, even if no local images found
        choices = create_image_choices(image_files)

        selected_image = handle_interactive_image_selection(
            choices,
            image_files,
            f"Select album art for: {album.artist} - {album.title}",
            album_artist=album.artist,
            album_title=album.title,
            output_dir=source_dir,
            allow_covit=True
        )

        if not selected_image:
            return

        # Store the selected image path for later processing
        album.custom['selected_album_art_source'] = str(selected_image)
        print(f"✅ Selected album art: {selected_image.name}")


# =============================================================================
# ALBUM ART ANALYSIS AND EMBEDDING
# =============================================================================

def embed_art_batch(files: List[Path], image_path: Path) -> tuple[int, int]:
    """Embed album art in a batch of files. Returns (success_count, total_count)."""
    success_count = 0
    total_count = len(files)

    for file_path in files:
        if AudioFileHandler.embed_art(file_path, image_path):
            success_count += 1
            print(f"✅ {file_path.name}")
        else:
            print(f"❌ {file_path.name}")

    return success_count, total_count


def remove_art_batch(files: List[Path]) -> int:
    """Remove embedded album art from a batch of files. Returns count of files with art removed."""
    removed_count = 0

    for file_path in files:
        if AudioFileHandler.remove_embedded_art(file_path):
            removed_count += 1

    return removed_count


def get_source_directory(tracks):
    """Get the common source directory for a list of tracks."""
    if not tracks:
        return None
    first_track_path = Path(tracks[0].path)
    return first_track_path.parent


def scan_directory_for_images(directory):
    """Scan a directory for image files and display their information."""
    image_files = PathUtils.scan_for_images(directory)

    if not image_files:
        print(f"🖼️  No image files found in: {directory}")
        return []

    print(f"🖼️  Image Files Found in: {directory}")

    for image_path in image_files:
        art_info = ImageProcessor.analyze_image_file(image_path)
        FormatUtils.print_album_art_info(image_path.name, art_info)

    return image_files


def analyze_image_file(image_path):
    """Analyze and display information about an image file."""
    art_info = ImageProcessor.analyze_image_file(image_path)
    FormatUtils.print_album_art_info(image_path.name, art_info)


# =============================================================================
# ONLINE FETCHING AND DOWNLOADING
# =============================================================================

def fetch_album_art_with_covit(artist: str, album: str, output_dir: Path) -> Optional[Path]:
    """Fetch album art using the covit command."""
    try:
        covit_path = Path(__file__).parent / "covit"
        if not covit_path.exists():
            print(f"❌ covit executable not found at {covit_path}")
            return None

        # Prepare the covit command
        cmd = [
            str(covit_path),
            "--address", "https://covers.musichoarders.xyz/",
            "--query-artist", artist,
            "--query-album", album
        ]

        print(f"🌐 Searching for album art: {artist} - {album}")
        print("   Please select an image from the web interface...")

        # Run covit and capture output
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )

            # Parse the JSON output (covit returns 1 even on success)
            output_lines = result.stdout.strip().split('\n')
            json_line = None
            for line in output_lines:
                if line.startswith('Picked: '):
                    json_line = line[8:]  # Remove 'Picked: ' prefix
                    break

            if not json_line:
                if result.returncode != 0 and result.stderr:
                    print(f"❌ covit command failed: {result.stderr}")
                else:
                    print("⏭️  No image was selected from the web interface.")
                return None

            try:
                picked_data = json.loads(json_line)
            except json.JSONDecodeError as e:
                print(f"❌ Failed to parse covit output: {e}")
                return None

            # Get the image URL (prefer big cover)
            image_url = picked_data.get('bigCoverUrl') or picked_data.get('smallCoverUrl')
            if not image_url:
                print("❌ No image URL found in covit response")
                return None

            # Get image info
            cover_info = picked_data.get('coverInfo', {})
            format_ext = cover_info.get('format', 'jpg')

            # Get source for filename
            source = picked_data.get('source', 'unknown')

            # Generate filename using covit source and save to temp directory
            filename = f"cover_{source}.{format_ext}"
            output_path = PathUtils.get_temp_art_dir() / filename

            # Download the image
            print(f"⬇️  Downloading album art...")
            with urllib.request.urlopen(image_url) as response:
                image_data = response.read()

            with open(output_path, 'wb') as f:
                f.write(image_data)

            print(f"✅ Downloaded album art: {output_path.name}")

            # Display info about the downloaded image
            art_info = ImageProcessor.analyze_image_data(image_data)
            FormatUtils.print_album_art_info(output_path.name, art_info)

            return output_path

        except subprocess.TimeoutExpired:
            print("❌ covit command timed out after 5 minutes")
            return None
        except Exception as e:
            print(f"❌ Error running covit: {e}")
            return None

    except Exception as e:
        print(f"❌ Error in fetch_album_art_with_covit: {e}")
        return None


def download_image_from_url(url: str) -> Optional[Path]:
    """Download an image from a URL and save it to a temporary location."""
    try:
        print(f"⬇️  Downloading image from URL...")

        # Download the image
        with urllib.request.urlopen(url) as response:
            image_data = response.read()

        # Try to determine format from the image data
        try:
            with Image.open(io.BytesIO(image_data)) as img:
                image_format = img.format.lower() if img.format else 'jpg'
        except Exception:
            # Fallback to guessing from URL
            if url.lower().endswith('.png'):
                image_format = 'png'
            elif url.lower().endswith('.gif'):
                image_format = 'gif'
            elif url.lower().endswith('.webp'):
                image_format = 'webp'
            else:
                image_format = 'jpg'

        # Generate filename and save to temp directory
        filename = f"downloaded_cover.{image_format}"
        output_path = PathUtils.get_temp_art_dir() / filename

        with open(output_path, 'wb') as f:
            f.write(image_data)

        print(f"✅ Downloaded image: {output_path.name}")

        # Display info about the downloaded image
        art_info = ImageProcessor.analyze_image_data(image_data)
        FormatUtils.print_album_art_info(output_path.name, art_info)

        return output_path

    except Exception as e:
        print(f"❌ Error downloading image from URL: {e}")
        return None


# =============================================================================
# USER INTERFACE COMPONENTS
# =============================================================================

def get_image_viewer_command():
    """Get the configured image viewer command."""
    try:
        return config.CONFIG.settings.album_art.image_viewer
    except (AttributeError, KeyError):
        return 'xdg-open {image_path}'


def open_image_viewer(image_path):
    """Open an image in the configured image viewer."""
    try:
        viewer_command = get_image_viewer_command()
        command = viewer_command.format(image_path=shlex.quote(str(image_path)))
        subprocess.Popen(shlex.split(command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        print(f"❌ Error opening image viewer: {e}")
        return False


def get_dialog_style():
    """Get the shared style for all prompt_toolkit components."""
    return Style.from_dict({
        'title': '#ansiblue bold',
        'question': '#ansiblue bold',
        'info': '#ansiyellow',
        'answer': '#ansigreen bold',
        'selected': '#ansigreen bold',
        'selected_fetch': '#ansicyan bold',
        'selected_skip': '#ansired bold',
        'normal': '',
    })


class BaseDialog:
    """Base class for prompt_toolkit dialogs."""

    def __init__(self):
        self.result = None

    def create_app(self) -> Application:
        """Create prompt_toolkit application with common setup."""
        layout = Layout(
            HSplit([
                Window(
                    content=FormattedTextControl(self.get_formatted_text),
                    wrap_lines=True,
                )
            ])
        )

        return Application(
            layout=layout,
            key_bindings=self.setup_keybindings(),
            style=get_dialog_style(),
            full_screen=False,
            mouse_support=False,
        )

    def run(self):
        """Run the dialog and return result."""
        try:
            app = self.create_app()
            app.run()
            return self.result
        except (KeyboardInterrupt, EOFError):
            print("\n❌ Operation cancelled by user")
            return self.get_cancel_result()

    def get_formatted_text(self) -> FormattedText:
        """Override in subclasses."""
        raise NotImplementedError

    def setup_keybindings(self) -> KeyBindings:
        """Override in subclasses."""
        raise NotImplementedError

    def get_cancel_result(self):
        """Override in subclasses to return appropriate cancel result."""
        return None

    def quit(self):
        """Quit the dialog."""
        self.result = self.get_cancel_result()
        get_app().exit()


class ImageSelector(BaseDialog):
    """Interactive image selector with arrow key navigation and preview."""

    def __init__(self, choices, image_files, prompt_text, album_artist=None, album_title=None, output_dir=None):
        super().__init__()
        self.choices = choices
        self.image_files = image_files
        self.prompt_text = prompt_text
        self.album_artist = album_artist
        self.album_title = album_title
        self.output_dir = output_dir
        self.selected_index = 0

    def get_formatted_text(self):
        """Generate the formatted text for the current state."""
        lines = [
            ("class:title", f"{self.prompt_text}\n"),
            ("class:info", "💡 Use ↑/↓ to navigate, Enter to select, 'o' to open/preview, 'c' to compress JPEG, 'r' to resize, 'q' to quit\n\n"),
        ]

        for i, choice in enumerate(self.choices):
            if i == self.selected_index:
                if i < len(self.image_files):
                    lines.append(("class:selected", f"❯ {choice}\n"))
                elif choice in ["🌐 Fetch album art online", "🔗 Enter image URL", "📁 Enter local file path"]:
                    lines.append(("class:selected_fetch", f"❯ {choice}\n"))
                else:
                    lines.append(("class:selected_skip", f"❯ {choice}\n"))
            else:
                lines.append(("class:normal", f"  {choice}\n"))

        return FormattedText(lines)

    def setup_keybindings(self):
        """Setup key bindings for image selector."""
        kb = KeyBindings()

        @kb.add('up')
        def move_up(event):
            if self.selected_index > 0:
                self.selected_index -= 1

        @kb.add('down')
        def move_down(event):
            if self.selected_index < len(self.choices) - 1:
                self.selected_index += 1

        @kb.add('enter')
        def select(event):
            self.result = self.choices[self.selected_index]
            get_app().exit()

        @kb.add('o')
        def preview(event):
            if self.selected_index < len(self.image_files):
                selected_image = self.image_files[self.selected_index]
                open_image_viewer(selected_image)

        @kb.add('c')
        def compress(event):
            if self.selected_index >= len(self.image_files):
                return

            selected_image = self.image_files[self.selected_index]

            # Check if it's a JPEG file
            if selected_image.suffix.lower() not in ['.jpg', '.jpeg']:
                return

            # Exit the current dialog to prompt for quality
            self.result = f"🗜️ Compress: {selected_image}"
            get_app().exit()

        @kb.add('r')
        def resize(event):
            if self.selected_index >= len(self.image_files):
                return

            selected_image = self.image_files[self.selected_index]

            # Exit the current dialog to prompt for size
            self.result = f"📐 Resize: {selected_image}"
            get_app().exit()

        @kb.add('q')
        def quit(event):
            self.quit()

        @kb.add('c-c')  # Single Ctrl+C for force exit
        def force_exit(event):
            print("\n🛑 Force exiting...")
            raise SystemExit(0)

        return kb

    def get_cancel_result(self):
        return self.choices[-1] if self.choices else None


class ConfirmDialog(BaseDialog):
    """Confirm dialog using prompt_toolkit."""

    def __init__(self, message: str, default: bool = True):
        super().__init__()
        self.message = message
        self.default = default
        self.result = default

    def get_formatted_text(self):
        """Generate the formatted text for the confirm dialog."""
        default_text = "Y/n" if self.default else "y/N"
        return FormattedText([
            ("class:question", f"{self.message} ({default_text}): "),
            ("class:answer", "Yes" if self.result else "No"),
            ("class:info", "\n💡 Use ←/→ to toggle, Enter to confirm, 'q' to quit")
        ])

    def setup_keybindings(self):
        """Setup key bindings for confirm dialog."""
        kb = KeyBindings()

        @kb.add('left')
        @kb.add('right')
        @kb.add(' ')
        def toggle(event):
            self.result = not self.result

        @kb.add('enter')
        def confirm(event):
            get_app().exit()

        @kb.add('y')
        def yes(event):
            self.result = True
            get_app().exit()

        @kb.add('n')
        def no(event):
            self.result = False
            get_app().exit()

        @kb.add('q')
        def quit(event):
            self.quit()

        @kb.add('c-c')  # Single Ctrl+C for force exit
        def force_exit(event):
            print("\n🛑 Force exiting...")
            raise SystemExit(0)

        return kb

    def get_cancel_result(self):
        return False


class SelectDialog(BaseDialog):
    """Select dialog using prompt_toolkit."""

    def __init__(self, message: str, choices: List[str]):
        super().__init__()
        self.message = message
        self.choices = choices
        self.selected_index = 0

    def get_formatted_text(self):
        """Generate the formatted text for the select dialog."""
        lines = [
            ("class:question", f"{self.message}\n"),
            ("class:info", "💡 Use ↑/↓ to navigate, Enter to select, 'q' to quit\n\n"),
        ]

        for i, choice in enumerate(self.choices):
            if i == self.selected_index:
                lines.append(("class:selected", f"❯ {choice}\n"))
            else:
                lines.append(("class:normal", f"  {choice}\n"))

        return FormattedText(lines)

    def setup_keybindings(self):
        """Setup key bindings for select dialog."""
        kb = KeyBindings()

        @kb.add('up')
        def move_up(event):
            if self.selected_index > 0:
                self.selected_index -= 1

        @kb.add('down')
        def move_down(event):
            if self.selected_index < len(self.choices) - 1:
                self.selected_index += 1

        @kb.add('enter')
        def select(event):
            self.result = self.choices[self.selected_index]
            get_app().exit()

        @kb.add('q')
        def quit(event):
            self.quit()

        @kb.add('c-c')  # Single Ctrl+C for force exit
        def force_exit(event):
            print("\n🛑 Force exiting...")
            raise SystemExit(0)

        return kb


def create_image_selector_with_preview(choices, image_files, prompt_text, album_artist=None, album_title=None, output_dir=None):
    """Create an interactive image selector with arrow key navigation and preview."""
    if not choices:
        return None

    selector = ImageSelector(choices, image_files, prompt_text, album_artist, album_title, output_dir)
    result = selector.run()

    if result is None:
        print("\n⏭️  Skipping album art embedding.")

    return result


def create_confirm_dialog(message: str, default: bool = True) -> bool:
    """Create a confirm dialog using prompt_toolkit."""
    dialog = ConfirmDialog(message, default)
    return dialog.run()


def create_select_dialog(message: str, choices: List[str]) -> Optional[str]:
    """Create a select dialog using prompt_toolkit."""
    if not choices:
        return None

    dialog = SelectDialog(message, choices)
    return dialog.run()


def create_image_choices(image_files: List[Path]) -> List[str]:
    """Create formatted choices for image selection."""
    choices = []
    for image_path in image_files:
        try:
            with Image.open(image_path) as img:
                width, height = img.size
                format_str = img.format
            file_size = FormatUtils.format_file_size(image_path.stat().st_size)
            choice_text = f"{image_path.name} ({width}×{height}, {format_str}, {file_size})"
            choices.append(choice_text)
        except Exception:
            choices.append(f"{image_path.name} (Unable to read)")

    choices.append("🌐 Fetch album art online")
    choices.append("🔗 Enter image URL")
    choices.append("📁 Enter local file path")
    choices.append("Skip embedding")
    return choices


# =============================================================================
# INTERACTIVE EMBEDDING AND SELECTION
# =============================================================================

def handle_interactive_image_selection(choices, image_files, prompt_text, album_artist=None, album_title=None, output_dir=None, allow_covit=True):
    """Handle the interactive image selection loop with online fetching, URL download, and file path validation."""
    while True:
        selected = create_image_selector_with_preview(
            choices,
            image_files,
            prompt_text,
            album_artist=album_artist,
            album_title=album_title,
            output_dir=output_dir
        )

        if selected == "Skip embedding" or not selected:
            print("⏭️  Skipping album art selection.")
            return None

        if selected.startswith("🗜️ Compress: "):
            # Extract the image path from the result
            image_path_str = selected[len("🗜️ Compress: "):]
            image_path = Path(image_path_str)

            # Check if it's a JPEG file
            if image_path.suffix.lower() not in ['.jpg', '.jpeg']:
                print(f"\n⚠️  Cannot compress {image_path.name}: Only JPEG files can be compressed")
                continue

            # Prompt for JPEG quality (now outside the prompt_toolkit application)
            print(f"\n🗜️  Compressing {image_path.name}...")
            try:
                quality_str = input("Enter JPEG quality (0-100, default 90): ")
                if not quality_str.strip():
                    quality = 90
                else:
                    quality = int(quality_str.strip())
                    if quality < 0 or quality > 100:
                        print(f"\n⚠️  Invalid quality value: {quality}. Must be between 0 and 100.")
                        continue
            except ValueError:
                print(f"\n⚠️  Invalid quality value: '{quality_str}'. Must be a number between 0 and 100.")
                continue
            except (KeyboardInterrupt, EOFError):
                print("\n⏭️  Compression cancelled.")
                continue

            # Perform compression
            compressed_path = ImageProcessor.compress_jpeg_image(image_path, quality)
            if compressed_path:
                # Add the compressed file to the list
                image_files.append(compressed_path)
                # Recreate choices with the new compressed file
                choices = create_image_choices(image_files)
                continue  # Restart the selection with updated choices
            else:
                continue  # Restart the selection if compression failed

        if selected.startswith("📐 Resize: "):
            # Extract the image path from the result
            image_path_str = selected[len("📐 Resize: "):]
            image_path = Path(image_path_str)

            # Prompt for image size (now outside the prompt_toolkit application)
            print(f"\n📐 Resizing {image_path.name}...")
            print("    Examples: 1750x1750 (maintain aspect ratio), 1750x1750! (ignore aspect ratio)")
            try:
                size_str = input("Enter image size (e.g., 1750x1750): ")
                if not size_str.strip():
                    print("\n⚠️  No size entered. Resize cancelled.")
                    continue
                size_str = size_str.strip()
            except (KeyboardInterrupt, EOFError):
                print("\n⏭️  Resize cancelled.")
                continue

            # Perform resize
            resized_path = ImageProcessor.resize_image(image_path, size_str)
            if resized_path:
                # Add the resized file to the list
                image_files.append(resized_path)
                # Recreate choices with the new resized file
                choices = create_image_choices(image_files)
                continue  # Restart the selection with updated choices
            else:
                continue  # Restart the selection if resize failed

        if selected == "🌐 Fetch album art online":
            if allow_covit and album_artist and album_title:
                fetched_image = fetch_album_art_with_covit(album_artist, album_title, output_dir)
                if fetched_image:
                    image_files.append(fetched_image)
                    choices = create_image_choices(image_files)
                    continue  # Restart the selection with updated choices
                else:
                    continue  # Restart the selection if covit failed
            else:
                if not allow_covit:
                    print("❌ Cannot fetch album art: Album information not available for files")
                else:
                    print("❌ Cannot fetch album art: Missing album information")
                continue

        if selected == "🔗 Enter image URL":
            url = prompt("Please enter the URL of the image you want to download: ")

            if url:
                downloaded_image = download_image_from_url(url)
                if downloaded_image:
                    image_files.append(downloaded_image)
                    choices = create_image_choices(image_files)
                    continue  # Restart the selection with updated choices
                else:
                    continue  # Restart the selection if download failed
            else:
                continue  # User cancelled, restart selection

        if selected == "📁 Enter local file path":
            file_path = prompt("Please enter the path to the image file: ")

            if file_path:
                validated_file = PathUtils.validate_local_image_file(file_path)
                if validated_file:
                    image_files.append(validated_file)
                    choices = create_image_choices(image_files)
                    continue  # Restart the selection with updated choices
                else:
                    continue  # Restart the selection if validation failed
            else:
                continue  # User cancelled, restart selection

        # Regular image selection
        try:
            selected_index = choices.index(selected)
            if selected_index >= len(image_files):
                continue
            selected_image = image_files[selected_index]
            return selected_image
        except ValueError:
            continue


def prompt_and_embed_album_art(tracks, image_files):
    """Prompt user to select and embed album art."""
    print(f"\n{'🎨 Album Art Embedding'}")
    print("=" * 25)

    # Remove all embedded album art first
    track_paths = [track.path for track in tracks]
    removed_count = remove_art_batch(track_paths)
    if removed_count > 0:
        print(f"🗑️  Removed embedded album art from {removed_count} track(s)")

    tracks_with_art = []
    tracks_without_art = []

    for track in tracks:
        art_info = AudioFileHandler.analyze_embedded_art(track.path)
        if art_info.has_art:
            tracks_with_art.append(track)
        else:
            tracks_without_art.append(track)

    # Since we removed all art, all tracks should be without art now
    target_tracks = tracks

    choices = create_image_choices(image_files)
    album = target_tracks[0].album if target_tracks else None
    source_dir = get_source_directory(target_tracks) if target_tracks else None

    selected_image = handle_interactive_image_selection(
        choices,
        image_files,
        f"Select album art for: {album.artist} - {album.title}",
        album_artist=album.artist,
        album_title=album.title,
        output_dir=source_dir,
        allow_covit=True
    )

    if not selected_image:
        return

    print(f"\n🎨 Embedding {selected_image.name} into {len(target_tracks)} track(s)...")

    success_count, total_count = embed_art_batch([track.path for track in target_tracks], selected_image)

    print(f"\n🎉 Successfully embedded album art in {success_count}/{total_count} tracks!")


def embed_art_for_files(audio_files: List[Path], image_files: List[Path]):
    """Embed album art for a list of audio files."""
    # Remove all embedded album art first
    removed_count = remove_art_batch(audio_files)
    if removed_count > 0:
        print(f"🗑️  Removed embedded album art from {removed_count} file(s)")

    choices = create_image_choices(image_files)
    output_dir = audio_files[0].parent if audio_files else None

    selected_image = handle_interactive_image_selection(
        choices,
        image_files,
        f"Select album art to embed in {len(audio_files)} file(s):",
        output_dir=output_dir,
        allow_covit=False
    )

    if not selected_image:
        return

    print(f"\n🎨 Embedding {selected_image.name} into {len(audio_files)} file(s)...")

    success_count, total_count = embed_art_batch(audio_files, selected_image)

    print(f"\n🎉 Successfully embedded album art in {success_count}/{total_count} files!")


# =============================================================================
# COMMAND LINE INTERFACE
# =============================================================================

@moe.hookimpl
def add_command(cmd_parsers: argparse._SubParsersAction):
    """Adds the 'albumart' command to Moe's CLI."""
    albumart_parser = cmd_parsers.add_parser(
        "albumart",
        description="Analyze and manage embedded album art in audio files.",
        help="analyze embedded album art"
    )
    albumart_parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing audio files to analyze"
    )
    albumart_parser.add_argument(
        "--embed",
        action="store_true",
        help="Interactively embed album art after analysis"
    )
    albumart_parser.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="Scan directory recursively"
    )
    albumart_parser.set_defaults(func=_parse_cli_args)


def _parse_cli_args(session: Session, args: argparse.Namespace):
    """Parse CLI arguments and execute the albumart command."""
    directory = args.directory.resolve()

    if not directory.exists():
        print(f"❌ Directory does not exist: {directory}")
        raise SystemExit(1)

    if not directory.is_dir():
        print(f"❌ Path is not a directory: {directory}")
        raise SystemExit(1)

    print(f"🔍 Analyzing album art in: {directory}")
    if args.recursive:
        print("📁 Scanning recursively...")

    analyze_directory_album_art(directory, args.recursive, args.embed)


def analyze_directory_album_art(directory: Path, recursive: bool = False, embed: bool = False):
    """Analyze album art in all audio files in a directory."""
    audio_extensions = {'.mp3', '.flac'}

    audio_files = []
    if recursive:
        for ext in audio_extensions:
            audio_files.extend(directory.rglob(f"*{ext}"))
    else:
        for ext in audio_extensions:
            audio_files.extend(directory.glob(f"*{ext}"))

    if not audio_files:
        print(f"🚫 No audio files found in {directory}")
        return

    # First, analyze what's there
    print(f"\n📀 Analyzing {len(audio_files)} audio file(s) for embedded album art...")
    files_with_art = []
    for audio_file in audio_files:
        art_info = AudioFileHandler.analyze_embedded_art(audio_file)
        if art_info.has_art:
            files_with_art.append(audio_file)

    # Ask for confirmation before removing embedded art
    if files_with_art:
        choice = create_confirm_dialog(
            f"Found embedded album art in {len(files_with_art)} file(s). Remove all embedded art before processing?"
        )
        if not choice:
            print("⏭️  Skipping album art removal and embedding.")
            return

        # Remove all embedded album art
        print(f"🗑️  Removing embedded album art from {len(files_with_art)} file(s)...")
        removed_count = remove_art_batch(files_with_art)
        if removed_count > 0:
            print(f"✅ Removed embedded album art from {removed_count} file(s)")
        else:
            print("⚠️  No embedded album art was successfully removed")

    print(f"\n📀 Found {len(audio_files)} audio file(s)")
    print("=" * 60)

    files_by_dir = defaultdict(list)
    for audio_file in sorted(audio_files):
        files_by_dir[audio_file.parent].append(audio_file)

    for dir_path, files in files_by_dir.items():
        if len(files_by_dir) > 1:
            print(f"\n📂 Directory: {dir_path}")
            print("-" * 50)

        tracks_without_art = []
        for audio_file in sorted(files):
            art_info = AudioFileHandler.analyze_embedded_art(audio_file)
            FormatUtils.print_album_art_info(audio_file.name, art_info)
            if not art_info.has_art:
                tracks_without_art.append(audio_file)

        if embed and tracks_without_art:
            image_files = scan_directory_for_images(dir_path)
            if image_files:
                choice = create_confirm_dialog(
                    f"\nEmbed album art for {len(tracks_without_art)} file(s) in {dir_path.name}?"
                )

                if choice:
                    embed_art_for_files(tracks_without_art, image_files)


# =============================================================================
# MOE INTEGRATION HOOKS
# =============================================================================

@moe.hookimpl
def add_config_validator(settings):
    """Add configuration validators for album_art plugin."""
    validators = [
        dynaconf.Validator("ALBUM_ART.IMAGE_VIEWER", default="xdg-open {image_path}"),
    ]
    settings.validators.register(*validators)


def find_best_album_art_extra(album) -> Optional[Extra]:
    """Find the best album art Extra file from an album's extras."""
    # Look for image files in the album's extras
    art_extra = None

    # First pass: look for preferred files with album/artist info or common names
    for extra in album.extras:
        if PathUtils.validate_image_extensions(extra.path):
            filename_lower = extra.path.name.lower()
            album_title_lower = album.title.lower()
            album_artist_lower = album.artist.lower() if album.artist else ""

            if (album_title_lower in filename_lower or
                album_artist_lower in filename_lower or
                "cover" in filename_lower or
                "folder" in filename_lower):
                art_extra = extra
                break

    # Second pass: if no preferred file found, just use any image file
    if not art_extra:
        for extra in album.extras:
            if PathUtils.validate_image_extensions(extra.path):
                art_extra = extra
                break

    return art_extra


@moe.hookimpl
def pre_add(item):
    """Select and potentially download album art before files are moved."""
    if not isinstance(item, Album):
        return

    album = item

    # Skip if we've already processed this album
    if 'album_art_processed' in album.custom:
        return

    # Mark this album as processed to avoid duplicate processing
    album.custom['album_art_processed'] = True

    # Get source directory from album path
    source_dir = album.path

    # Analyze existing embedded art in all tracks (for summary only)
    if album.tracks:
        tracks_with_art = 0
        tracks_without_art = 0

        for track in album.tracks:
            art_info = AudioFileHandler.analyze_embedded_art(track.path)
            if art_info.has_art:
                tracks_with_art += 1
            else:
                tracks_without_art += 1

        print(f"📊 {len(album.tracks)} tracks total: {tracks_with_art} with embedded art, {tracks_without_art} without")
        if tracks_with_art > 0:
            print("🗑️  All embedded art will be removed before applying selected album art")

    # Look for image files in the source directory
    image_files = scan_directory_for_images(source_dir)

    # Always present the interactive selection interface, even if no local images found
    choices = create_image_choices(image_files)

    selected_image = handle_interactive_image_selection(
        choices,
        image_files,
        f"Select album art for: {album.artist} - {album.title}",
        album_artist=album.artist,
        album_title=album.title,
        output_dir=source_dir,
        allow_covit=True
    )

    if not selected_image:
        return

    # Store the selected image path for later processing
    album.custom['selected_album_art_source'] = str(selected_image)
    print(f"✅ Selected album art: {selected_image.name}")


@moe.hookimpl(tryfirst=True)
def edit_new_items(session: Session, items):
    """Create album art Extra files before organize_extras runs."""
    albums_to_process = []

    # Collect albums that need art files created
    for item in items:
        if isinstance(item, Album) and 'selected_album_art_source' in item.custom:
            albums_to_process.append(item)

    # Create Extra files for selected album art
    for album in albums_to_process:
        source_path = Path(album.custom['selected_album_art_source'])

        if not source_path.exists():
            print(f"⚠️  Selected album art no longer exists: {source_path}")
            del album.custom['selected_album_art_source']
            continue

        # Check if the selected image is already an Extra in the album
        existing_extra = None
        for extra in album.extras:
            if extra.path == source_path:
                existing_extra = extra
                break

        if existing_extra:
            # The selected image is already an Extra, just mark it for embedding
            print(f"✅ Using existing album art extra: {source_path.name}")
        else:
            # Create a new Extra for the original file - organize_extras will handle renaming
            art_extra = Extra(album, source_path)
            items.append(art_extra)
            print(f"✅ Added album art extra: {source_path.name}")

        # Store reference for embedding later (we'll find the organized file)
        album.custom['album_art_selected'] = True
        # Clean up the source reference
        del album.custom['selected_album_art_source']


@moe.hookimpl
def process_new_items(session: Session, items):
    """Embed album art after files have been moved and organized."""
    albums_to_process = []

    # Find albums that have art to embed
    for item in items:
        if isinstance(item, Album) and 'album_art_selected' in item.custom:
            albums_to_process.append(item)

    # Embed art in each album's tracks
    for album in albums_to_process:
        tracks = album.tracks
        art_extra = find_best_album_art_extra(album)

        if art_extra and art_extra.path.exists():
            # Remove all embedded album art from destination files first
            track_paths = [track.path for track in tracks]
            removed_count = remove_art_batch(track_paths)
            if removed_count > 0:
                print(f"\n🗑️  Removed embedded album art from {removed_count} destination track(s)")

            print(f"\n🎨 Embedding {art_extra.path.name} into {len(tracks)} track(s)...")

            success_count, total_count = embed_art_batch([track.path for track in tracks], art_extra.path)

            print(f"🎉 Successfully embedded album art in {success_count}/{total_count} tracks!")
        else:
            # Art file might have been filtered out or not found, that's okay
            print(f"ℹ️  No album art file found for embedding in: {album.artist} - {album.title}")
            # This could happen if the user filtered out the art file in filter_extras

        # Clean up custom fields
        if 'album_art_selected' in album.custom:
            del album.custom['album_art_selected']
        if 'album_art_processed' in album.custom:
            del album.custom['album_art_processed']
