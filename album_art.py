import argparse
import io
import json
import shlex
import subprocess
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
from moe.library import Album, Track


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

    print(f"🎨 {file_name}: Album art found")

    if art_info.width and art_info.height and art_info.format:
        print(f"   📐 Dimensions: {art_info.width}×{art_info.height} pixels")
        print(f"   🖼️  Format: {art_info.format}")

    if art_info.size_bytes:
        size_str = format_file_size(art_info.size_bytes)
        print(f"   📊 Size: {size_str}")

    if art_info.error:
        print(f"   ❌ Could not analyze image details: {art_info.error}")


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
            print(f"\n🖼️  No image files found in: {directory}")
            return []

        print(f"\n🖼️  Image Files Found in: {directory}")
        print("-" * 50)

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
        size_str = format_file_size(file_size)

        with Image.open(image_path) as image:
            width, height = image.size
            image_format = image.format

        filename_lower = image_path.name.lower()
        is_likely_cover = any(term in filename_lower for term in [
            'cover', 'front', 'folder', 'album', 'artwork'
        ])

        cover_indicator = "🎨" if is_likely_cover else "🖼️ "

        print(f"{cover_indicator} {image_path.name}")
        print(f"   📐 Dimensions: {width}×{height} pixels")
        print(f"   🖼️  Format: {image_format}")
        print(f"   📊 Size: {size_str}")

        pixel_count = width * height
        if pixel_count >= 1000000:
            quality = "High resolution"
        elif pixel_count >= 500000:
            quality = "Good resolution"
        elif pixel_count >= 250000:
            quality = "Medium resolution"
        else:
            quality = "Low resolution"

        print(f"   ⭐ Quality: {quality}")

        aspect_ratio = width / height
        if 0.9 <= aspect_ratio <= 1.1:
            if pixel_count >= 500000:
                print(f"   ✅ Recommended for album art")
            else:
                print(f"   ⚠️  Small for album art")
        else:
            print(f"   ⚠️  Non-square aspect ratio ({aspect_ratio:.2f}:1)")
        print()

    except Exception as e:
        try:
            file_size = image_path.stat().st_size
            size_str = format_file_size(file_size)
            print(f"🖼️  {image_path.name} ({size_str})")
            print(f"   ❌ Could not analyze image: {e}")
            print()
        except Exception as e2:
            print(f"❌ {image_path.name}: Error reading file - {e2}")
            print()


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

            # Generate filename
            safe_artist = "".join(c for c in artist if c.isalnum() or c in (' ', '-', '_')).strip()
            safe_album = "".join(c for c in album if c.isalnum() or c in (' ', '-', '_')).strip()
            filename = f"{safe_artist} - {safe_album}_cover.{format_ext}"
            output_path = output_dir / filename

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
                    print(f"   📐 Dimensions: {width}×{height} pixels")
                    print(f"   🖼️  Format: {img.format}")
                    print(f"   📊 Size: {format_file_size(len(image_data))}")
            except Exception:
                pass

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
# IMPORT HOOK - Integration with Moe's import system
# =============================================================================

@moe.hookimpl
def add_config_validator(settings):
    """Add configuration validators for album_art plugin."""
    validators = [
        dynaconf.Validator("ALBUM_ART.IMAGE_VIEWER", default="xdg-open {image_path}"),
    ]
    settings.validators.register(*validators)


@moe.hookimpl
def process_new_items(session: Session, items):
    """Analyze album art in tracks when they're being imported/added."""
    albums_to_tracks = defaultdict(list)

    for item in items:
        if isinstance(item, Track):
            albums_to_tracks[item.album._id].append(item)

    for album_id, tracks in albums_to_tracks.items():
        if not tracks:
            continue

        album = tracks[0].album
        print(f"\n{'='*60}")
        print(f"🎵 Album: {album.artist} - {album.title}")
        print(f"{'='*60}")

        print("\n📀 Embedded Album Art Analysis:")
        print("-" * 35)
        for track in tracks:
            art_info = analyze_audio_file_album_art(track.path)
            print_album_art_info(track.path.name, art_info)

        source_dir = get_source_directory(tracks)
        if source_dir:
            image_files = scan_directory_for_images(source_dir)
            if image_files:
                prompt_and_embed_album_art(tracks, image_files)
