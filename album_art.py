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
import questionary
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

import moe
from moe import config
from moe.library import Album, Extra


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


# =============================================================================
# ANALYSIS - Core album art detection and analysis functionality
# =============================================================================

def analyze_audio_file_album_art(audio_file_path: Path) -> AlbumArtInfo:
    """Analyze album art in an audio file and return structured information."""
    try:
        suffix = audio_file_path.suffix.lower()
        album_art_data = None

        if suffix == '.flac':
            flac_file = FLAC(audio_file_path)
            if flac_file.pictures:
                album_art_data = flac_file.pictures[0].data
        elif suffix == '.mp3':
            try:
                id3_file = ID3(audio_file_path)
                for key in id3_file.keys():
                    if key.startswith('APIC:'):
                        album_art_data = id3_file[key].data
                        break
            except ID3NoHeaderError:
                pass
        else:
            return AlbumArtInfo(has_art=False, error=f"Unsupported format: {suffix}")

        if album_art_data:
            try:
                image = Image.open(io.BytesIO(album_art_data))
                return AlbumArtInfo(
                    has_art=True,
                    image_data=album_art_data,
                    width=image.size[0],
                    height=image.size[1],
                    format=image.format,
                    size_bytes=len(album_art_data)
                )
            except Exception as e:
                return AlbumArtInfo(
                    has_art=True,
                    image_data=album_art_data,
                    size_bytes=len(album_art_data),
                    error=str(e)
                )
        else:
            return AlbumArtInfo(has_art=False)

    except Exception as e:
        return AlbumArtInfo(has_art=False, error=str(e))


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
        size_str = format_file_size(art_info.size_bytes)
        details.append(size_str)

    # Create the single line output
    details_str = " | ".join(details) if details else "details unavailable"

    if art_info.error:
        print(f"🎨 {file_name}: Album art found ({details_str}) - Warning: {art_info.error}")
    else:
        print(f"🎨 {file_name}: Album art found ({details_str})")


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


def embed_album_art_in_file(audio_file_path: Path, image_path: Path) -> bool:
    """Embed album art into an audio file."""
    try:
        with open(image_path, 'rb') as f:
            image_data = f.read()

        mime_type = get_mime_type(image_path)
        suffix = audio_file_path.suffix.lower()

        if suffix == '.flac':
            return _embed_flac_art(audio_file_path, image_data, mime_type)
        elif suffix == '.mp3':
            return _embed_id3_art(audio_file_path, image_data, mime_type)
        else:
            print(f"⚠️  Unsupported audio format: {suffix}")
            return False

    except Exception as e:
        print(f"❌ Error embedding art: {e}")
        return False


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


def _embed_id3_art(audio_file_path: Path, image_data: bytes, mime_type: str) -> bool:
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


def get_mime_type(image_path):
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


# =============================================================================
# DIRECTORY SCANNING - Finding and analyzing image files
# =============================================================================

def scan_directory_for_images(directory):
    """Scan a directory for image files and display their information."""
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp'}

    try:
        image_files = []
        for file_path in directory.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                image_files.append(file_path)

        if not image_files:
            print(f"🖼️  No image files found in: {directory}")
            return []

        print(f"🖼️  Image Files Found in: {directory}")

        image_files.sort(key=lambda x: x.stat().st_size, reverse=True)

        for image_path in image_files:
            analyze_image_file(image_path)

        return image_files

    except Exception as e:
        print(f"❌ Error scanning directory {directory}: {e}")
        return []


def analyze_image_file(image_path):
    """Analyze and display information about an image file."""
    try:
        file_size = image_path.stat().st_size

        with Image.open(image_path) as image:
            width, height = image.size
            image_format = image.format

        # Create AlbumArtInfo for consistency
        art_info = AlbumArtInfo(
            has_art=True,
            width=width,
            height=height,
            format=image_format,
            size_bytes=file_size
        )

        print_album_art_info(image_path.name, art_info)

    except Exception as e:
        try:
            file_size = image_path.stat().st_size
            # Create AlbumArtInfo with error
            art_info = AlbumArtInfo(
                has_art=True,
                size_bytes=file_size,
                error=str(e)
            )
            print_album_art_info(image_path.name, art_info)
        except Exception as e2:
            # Create AlbumArtInfo with error for file access issues
            art_info = AlbumArtInfo(
                has_art=False,
                error=f"Error reading file - {e2}"
            )
            print_album_art_info(image_path.name, art_info)


def get_source_directory(tracks):
    """Get the common source directory for a list of tracks."""
    if not tracks:
        return None
    first_track_path = Path(tracks[0].path)
    return first_track_path.parent


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
            filename = f"cover_covit_{source}.{format_ext}"
            # Use temporary directory instead of modifying source directory
            temp_dir = Path(tempfile.gettempdir()) / "moe_album_art"
            temp_dir.mkdir(exist_ok=True)
            output_path = temp_dir / filename

            # Download the image
            print(f"⬇️  Downloading album art...")
            with urllib.request.urlopen(image_url) as response:
                image_data = response.read()

            with open(output_path, 'wb') as f:
                f.write(image_data)

            print(f"✅ Downloaded album art: {output_path.name}")

            # Display info about the downloaded image
            try:
                with Image.open(output_path) as img:
                    width, height = img.size
                    # Create AlbumArtInfo for consistency
                    art_info = AlbumArtInfo(
                        has_art=True,
                        width=width,
                        height=height,
                        format=img.format,
                        size_bytes=len(image_data)
                    )
                    print_album_art_info(output_path.name, art_info)
            except Exception as e:
                # Create AlbumArtInfo with error
                art_info = AlbumArtInfo(
                    has_art=True,
                    size_bytes=len(image_data),
                    error=str(e)
                )
                print_album_art_info(output_path.name, art_info)

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


# =============================================================================
# INTERACTIVE EMBEDDING - User interaction and embedding functionality
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


def create_image_selector_with_preview(choices, image_files, prompt_text, album_artist=None, album_title=None, output_dir=None):
    """Create an interactive image selector with arrow key navigation and preview."""
    if not choices:
        return None

    class ImageSelector:
        def __init__(self, choices, image_files, prompt_text, album_artist=None, album_title=None, output_dir=None):
            self.choices = choices
            self.image_files = image_files
            self.prompt_text = prompt_text
            self.album_artist = album_artist
            self.album_title = album_title
            self.output_dir = output_dir
            self.selected_index = 0
            self.result = None

        def get_formatted_text(self):
            """Generate the formatted text for the current state."""
            lines = [
                ("class:title", f"{self.prompt_text}\n"),
                ("class:info", "💡 Use ↑/↓ to navigate, Enter to select, 'v' to preview, 'q' to quit\n\n"),
            ]

            for i, choice in enumerate(self.choices):
                if i == self.selected_index:
                    if i < len(self.image_files):
                        lines.append(("class:selected", f"❯ {choice}\n"))
                    elif choice == "🌐 Fetch album art online":
                        lines.append(("class:selected_fetch", f"❯ {choice}\n"))
                    else:
                        lines.append(("class:selected_skip", f"❯ {choice}\n"))
                else:
                    lines.append(("class:normal", f"  {choice}\n"))

            return FormattedText(lines)

        def move_up(self):
            if self.selected_index > 0:
                self.selected_index -= 1

        def move_down(self):
            if self.selected_index < len(self.choices) - 1:
                self.selected_index += 1

        def select_current(self):
            selected_choice = self.choices[self.selected_index]
            self.result = selected_choice
            get_app().exit()

        def preview_current(self):
            if self.selected_index < len(self.image_files):
                selected_image = self.image_files[self.selected_index]
                open_image_viewer(selected_image)

        def quit(self):
            self.result = self.choices[-1]  # Skip option
            get_app().exit()

    selector = ImageSelector(choices, image_files, prompt_text, album_artist, album_title, output_dir)

    # Create key bindings
    kb = KeyBindings()

    @kb.add('up')
    def move_up(event):
        selector.move_up()

    @kb.add('down')
    def move_down(event):
        selector.move_down()

    @kb.add('enter')
    def select(event):
        selector.select_current()

    @kb.add('v')
    def preview(event):
        selector.preview_current()

    @kb.add('q')
    @kb.add('c-c')  # Ctrl+C
    def quit(event):
        selector.quit()

    # Create the layout
    def get_content():
        return selector.get_formatted_text()

    layout = Layout(
        HSplit([
            Window(
                content=FormattedTextControl(get_content),
                wrap_lines=True,
            )
        ])
    )

    # Custom style
    from prompt_toolkit.styles import Style

    style = Style.from_dict({
        'title': '#ansiblue bold',
        'info': '#ansiyellow',
        'selected': '#ansigreen bold',
        'selected_fetch': '#ansicyan bold',
        'selected_skip': '#ansired bold',
        'normal': '',
    })

    # Create and run the application
    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=False,
        mouse_support=False,
    )

    try:
        app.run()
        return selector.result
    except (KeyboardInterrupt, EOFError):
        print("\n⏭️  Skipping album art embedding.")
        return choices[-1]


def create_image_choices(image_files: List[Path]) -> List[str]:
    """Create formatted choices for image selection."""
    choices = []
    for image_path in image_files:
        try:
            with Image.open(image_path) as img:
                width, height = img.size
                format_str = img.format
            file_size = format_file_size(image_path.stat().st_size)
            choice_text = f"{image_path.name} ({width}×{height}, {format_str}, {file_size})"
            choices.append(choice_text)
        except Exception:
            choices.append(f"{image_path.name} (Unable to read)")

    choices.append("🌐 Fetch album art online")
    choices.append("Skip embedding")
    return choices


def prompt_and_embed_album_art(tracks, image_files):
    """Prompt user to select and embed album art."""
    print(f"\n{'🎨 Album Art Embedding'}")
    print("=" * 25)

    tracks_with_art = []
    tracks_without_art = []

    for track in tracks:
        art_info = analyze_audio_file_album_art(track.path)
        if art_info.has_art:
            tracks_with_art.append(track)
        else:
            tracks_without_art.append(track)

    if tracks_with_art and not tracks_without_art:
        choice = questionary.confirm(
            f"All {len(tracks)} tracks already have embedded album art. Replace it?"
        ).ask()
        if not choice:
            print("⏭️  Skipping album art embedding.")
            return
        target_tracks = tracks
    elif tracks_with_art:
        choice = questionary.select(
            f"{len(tracks_with_art)} tracks have art, {len(tracks_without_art)} don't. What to do?",
            choices=[
                "Embed in tracks without art only",
                "Replace art in all tracks",
                "Skip embedding"
            ]
        ).ask()

        if choice == "Skip embedding":
            print("⏭️  Skipping album art embedding.")
            return
        elif choice == "Embed in tracks without art only":
            target_tracks = tracks_without_art
        else:
            target_tracks = tracks
    else:
        target_tracks = tracks

    choices = create_image_choices(image_files)
    album = target_tracks[0].album if target_tracks else None
    source_dir = get_source_directory(target_tracks) if target_tracks else None

    # Handle covit integration outside the prompt_toolkit loop
    while True:
        selected = create_image_selector_with_preview(
            choices,
            image_files,
            f"Select album art to embed in {len(target_tracks)} track(s):",
            album_artist=album.artist if album else None,
            album_title=album.title if album else None,
            output_dir=source_dir
        )

        if selected == "Skip embedding" or not selected:
            print("⏭️  Skipping album art embedding.")
            return

        if selected == "🌐 Fetch album art online":
            if album and album.artist and album.title and source_dir:
                fetched_image = fetch_album_art_with_covit(album.artist, album.title, source_dir)
                if fetched_image:
                    image_files.append(fetched_image)
                    choices = create_image_choices(image_files)
                    continue  # Restart the selection with updated choices
                else:
                    continue  # Restart the selection if covit failed
            else:
                print("❌ Cannot fetch album art: Missing album information")
                continue

        # Regular image selection
        try:
            selected_index = choices.index(selected)
            if selected_index >= len(image_files):
                continue
            selected_image = image_files[selected_index]
            break
        except ValueError:
            continue

    print(f"\n🎨 Embedding {selected_image.name} into {len(target_tracks)} track(s)...")

    success_count = 0
    for track in target_tracks:
        if embed_album_art_in_file(track.path, selected_image):
            success_count += 1
            print(f"✅ {track.path.name}")
        else:
            print(f"❌ {track.path.name}")

    print(f"\n🎉 Successfully embedded album art in {success_count}/{len(target_tracks)} tracks!")


def embed_art_for_files(audio_files: List[Path], image_files: List[Path]):
    """Embed album art for a list of audio files."""
    choices = create_image_choices(image_files)
    output_dir = audio_files[0].parent if audio_files else None

    # Handle covit integration outside the prompt_toolkit loop
    while True:
        selected = create_image_selector_with_preview(
            choices,
            image_files,
            f"Select album art to embed in {len(audio_files)} file(s):",
            output_dir=output_dir
        )

        if selected == "Skip embedding" or not selected:
            print("⏭️  Skipping album art embedding.")
            return

        if selected == "🌐 Fetch album art online":
            print("❌ Cannot fetch album art: Album information not available for files")
            continue

        # Regular image selection
        try:
            selected_index = choices.index(selected)
            if selected_index >= len(image_files):
                continue
            selected_image = image_files[selected_index]
            break
        except ValueError:
            continue

    print(f"\n🎨 Embedding {selected_image.name} into {len(audio_files)} file(s)...")

    success_count = 0
    for audio_file in audio_files:
        if embed_album_art_in_file(audio_file, selected_image):
            success_count += 1
            print(f"✅ {audio_file.name}")
        else:
            print(f"❌ {audio_file.name}")

    print(f"\n🎉 Successfully embedded album art in {success_count}/{len(audio_files)} files!")


# =============================================================================
# CLI COMMAND - Command-line interface implementation
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
            art_info = analyze_audio_file_album_art(audio_file)
            print_album_art_info(audio_file.name, art_info)
            if not art_info.has_art:
                tracks_without_art.append(audio_file)

        if embed and tracks_without_art:
            image_files = scan_directory_for_images(dir_path)
            if image_files:
                choice = questionary.confirm(
                    f"\nEmbed album art for {len(tracks_without_art)} file(s) in {dir_path.name}?"
                ).ask()

                if choice:
                    embed_art_for_files(tracks_without_art, image_files)


# =============================================================================
# IMPORT HOOKS - Integration with Moe's import system
# =============================================================================

@moe.hookimpl
def add_config_validator(settings):
    """Add configuration validators for album_art plugin."""
    validators = [
        dynaconf.Validator("ALBUM_ART.IMAGE_VIEWER", default="xdg-open {image_path}"),
    ]
    settings.validators.register(*validators)


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

    print("\n📀 Embedded Album Art Analysis:")
    print("-" * 35)

    # Analyze existing embedded art in all tracks
    if album.tracks:
        # Sort tracks by disc and track number for consistent display
        sorted_tracks = sorted(album.tracks, key=lambda t: (t.disc, t.track_num))
        for track in sorted_tracks:
            art_info = analyze_audio_file_album_art(track.path)
            print_album_art_info(track.path.name, art_info)

    # Look for image files in the source directory
    image_files = scan_directory_for_images(source_dir)

    if image_files:
        # Use the interactive selection interface
        choices = create_image_choices(image_files)

        # Handle covit integration outside the prompt_toolkit loop
        while True:
            selected = create_image_selector_with_preview(
                choices,
                image_files,
                f"Select album art for: {album.artist} - {album.title}",
                album_artist=album.artist,
                album_title=album.title,
                output_dir=source_dir
            )

            if selected == "Skip embedding" or not selected:
                print("⏭️  Skipping album art selection.")
                return

            if selected == "🌐 Fetch album art online":
                if album and album.artist and album.title:
                    fetched_image = fetch_album_art_with_covit(album.artist, album.title, source_dir)
                    if fetched_image:
                        image_files.append(fetched_image)
                        choices = create_image_choices(image_files)
                        continue  # Restart the selection with updated choices
                    else:
                        continue  # Restart the selection if covit failed
                else:
                    print("❌ Cannot fetch album art: Missing album information")
                    continue

            # Regular image selection
            try:
                selected_index = choices.index(selected)
                if selected_index >= len(image_files):
                    continue
                selected_image = image_files[selected_index]
                break
            except ValueError:
                continue

        # Store the selected image path for later processing
        album.custom['selected_album_art_source'] = str(selected_image)
        print(f"✅ Selected album art: {selected_image.name}")


@moe.hookimpl
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

        # Find the album art Extra file
        # Look for image files in the album's extras
        art_extra = None

        for extra in album.extras:
            # Look for any image file - prioritize files with album title in name
            if extra.path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}:
                # If the filename contains the album title or artist, prefer it
                filename_lower = extra.path.name.lower()
                album_title_lower = album.title.lower()
                album_artist_lower = album.artist.lower() if album.artist else ""

                if (album_title_lower in filename_lower or
                    album_artist_lower in filename_lower or
                    "cover" in filename_lower or
                    "folder" in filename_lower):
                    art_extra = extra
                    break

        # If no preferred file found, just use any image file
        if not art_extra:
            for extra in album.extras:
                if extra.path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}:
                    art_extra = extra
                    break

        if art_extra and art_extra.path.exists():
            print(f"\n🎨 Embedding {art_extra.path.name} into {len(tracks)} track(s)...")

            success_count = 0
            for track in tracks:
                if embed_album_art_in_file(track.path, art_extra.path):
                    success_count += 1
                    print(f"✅ {track.path.name}")
                else:
                    print(f"❌ {track.path.name}")

            print(f"🎉 Successfully embedded album art in {success_count}/{len(tracks)} tracks!")
        else:
            # Art file might have been filtered out or not found, that's okay
            print(f"ℹ️  No album art file found for embedding in: {album.artist} - {album.title}")
            # This could happen if the user filtered out the art file in filter_extras

        # Clean up custom fields
        if 'album_art_selected' in album.custom:
            del album.custom['album_art_selected']
        if 'album_art_processed' in album.custom:
            del album.custom['album_art_processed']
