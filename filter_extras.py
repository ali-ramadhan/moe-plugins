"""
Filter extra files plugin for Moe.

This plugin provides an interactive interface to filter extra files during import.
Users can select which extra files to keep using checkboxes.
"""

import logging
import subprocess
from collections import defaultdict
from typing import List, Set

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.application import get_app
from prompt_toolkit.styles import Style
from sqlalchemy.orm.session import Session

import moe
import moe.config
from moe.library import Extra, Album

__all__ = []

log = logging.getLogger("moe.filter_extras")


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


def get_file_info(extra: Extra) -> str:
    """Get formatted file information for display."""
    try:
        size_bytes = extra.path.stat().st_size
        size_str = format_file_size(size_bytes)
        return f"{extra.rel_path} ({size_str})"
    except (OSError, AttributeError):
        return str(extra.rel_path)


class ExtrasFilterSelector:
    """Interactive selector for filtering extra files using checkboxes."""

    def __init__(self, extras: List[Extra], album: Album):
        self.extras = extras
        self.album = album
        self.selected_extras: Set[int] = set(range(len(extras)))  # All selected by default
        self.current_index = 0
        self.result = None

    def get_formatted_text(self):
        """Generate the formatted text for the current state."""
        artist = self.album.artist or "Unknown Artist"
        title = self.album.title or "Unknown Album"

        # Check if text editor is configured
        try:
            text_editor = moe.config.CONFIG.settings.filter_extras.text_editor
        except (AttributeError, KeyError):
            text_editor = None

        if text_editor:
            help_text = "💡 Use ↑/↓ to navigate, Space to toggle, Enter to confirm, 'a' to select all, 'n' to select none, 'o' to open, 't' for text editor, 'q' to quit\n\n"
        else:
            help_text = "💡 Use ↑/↓ to navigate, Space to toggle, Enter to confirm, 'a' to select all, 'n' to select none, 'o' to open, 'q' to quit\n\n"

        lines = [
            ("class:title", f"Filter extra files for: {artist} - {title}\n"),
            ("class:info", help_text),
        ]

        for i, extra in enumerate(self.extras):
            file_info = get_file_info(extra)
            checkbox = "☑" if i in self.selected_extras else "☐"

            if i == self.current_index:
                lines.append(("class:selected", f"❯ {checkbox} {file_info}\n"))
            else:
                lines.append(("class:normal", f"  {checkbox} {file_info}\n"))

        selected_count = len(self.selected_extras)
        total_count = len(self.extras)
        lines.append(("class:summary", f"\nSelected: {selected_count}/{total_count} files\n"))

        return FormattedText(lines)

    def move_up(self):
        if self.current_index > 0:
            self.current_index -= 1

    def move_down(self):
        if self.current_index < len(self.extras) - 1:
            self.current_index += 1

    def toggle_current(self):
        if self.current_index in self.selected_extras:
            self.selected_extras.remove(self.current_index)
        else:
            self.selected_extras.add(self.current_index)

    def select_all(self):
        self.selected_extras = set(range(len(self.extras)))

    def select_none(self):
        self.selected_extras.clear()

    def open_current_file(self):
        """Open the currently selected file using xdg-open."""
        if 0 <= self.current_index < len(self.extras):
            current_extra = self.extras[self.current_index]
            try:
                subprocess.run(
                    ['xdg-open', str(current_extra.path)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                log.debug(f"Opened file: {current_extra.path}")
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                log.error(f"Failed to open file {current_extra.path}: {e}")

    def open_current_file_in_editor(self):
        """Open the currently selected file in a text editor."""
        if 0 <= self.current_index < len(self.extras):
            current_extra = self.extras[self.current_index]

            # Get text editor from config
            try:
                text_editor = moe.config.CONFIG.settings.filter_extras.text_editor
            except (AttributeError, KeyError):
                text_editor = None

            if text_editor:
                try:
                    subprocess.run(
                        [text_editor, str(current_extra.path)],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    log.debug(f"Opened file in editor: {current_extra.path}")
                except (subprocess.SubprocessError, FileNotFoundError) as e:
                    log.error(f"Failed to open file in editor {current_extra.path}: {e}")
            else:
                log.warning("No text editor configured in moe config")

    def confirm_selection(self):
        self.result = [self.extras[i] for i in self.selected_extras]
        get_app().exit()

    def quit(self):
        self.result = self.extras  # Keep all extras if quitting
        get_app().exit()


def create_extras_filter_interface(extras: List[Extra], album: Album) -> List[Extra]:
    """Create an interactive filter interface for extra files."""
    if not extras:
        return extras

    selector = ExtrasFilterSelector(extras, album)

    # Create key bindings
    kb = KeyBindings()

    @kb.add('up')
    def move_up(event):
        selector.move_up()

    @kb.add('down')
    def move_down(event):
        selector.move_down()

    @kb.add('space')
    def toggle(event):
        selector.toggle_current()

    @kb.add('a')
    def select_all(event):
        selector.select_all()

    @kb.add('n')
    def select_none(event):
        selector.select_none()

    @kb.add('o')
    def open_file(event):
        selector.open_current_file()

    # Only add text editor binding if configured
    try:
        text_editor = moe.config.CONFIG.settings.filter_extras.text_editor
    except (AttributeError, KeyError):
        text_editor = None

    if text_editor:
        @kb.add('t')
        def open_in_editor(event):
            selector.open_current_file_in_editor()

    @kb.add('enter')
    def confirm(event):
        selector.confirm_selection()

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
    style = Style.from_dict({
        'title': '#ansiblue bold',
        'info': '#ansiyellow',
        'selected': '#ansigreen bold',
        'normal': '',
        'summary': '#ansicyan',
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
        return selector.result if selector.result is not None else extras
    except (KeyboardInterrupt, EOFError):
        print("\n⏭️  Keeping all extra files.")
        return extras


@moe.hookimpl
def edit_new_items(session: Session, items):
    """Filter extra files before they are finalized in the database."""
    # Group extras by album object ID to handle new albums and avoid hashability issues
    albums_with_extras = defaultdict(list)
    album_id_to_album = {}  # Map album IDs back to album objects

    for item in items:
        if isinstance(item, Extra):
            album_obj_id = id(item.album)
            albums_with_extras[album_obj_id].append(item)
            album_id_to_album[album_obj_id] = item.album

    # Keep track of extras to remove
    extras_to_remove_global = []

    # Process each album's extras
    for album_obj_id, extras in albums_with_extras.items():
        if len(extras) <= 1:
            # Skip filtering if there's only one or no extras
            continue

        album = album_id_to_album[album_obj_id]
        print(f"\n📁 Found {len(extras)} extra files for: {album.artist} - {album.title}")

        # Show the interactive filter interface
        selected_extras = create_extras_filter_interface(extras, album)

        # Identify extras to remove
        extras_to_remove = [extra for extra in extras if extra not in selected_extras]

        for extra in extras_to_remove:
            log.debug(f"Removing extra: {extra.rel_path}")
            album.extras.remove(extra)
            extras_to_remove_global.append(extra)

        if extras_to_remove:
            removed_count = len(extras_to_remove)
            kept_count = len(selected_extras)
            print(f"✅ Kept {kept_count} extra files, removed {removed_count} extra files")

            # Show which files were kept
            for extra in selected_extras:
                print(f"   ✅ {extra.rel_path}")
        else:
            print("✅ All extra files kept")

    # Remove the filtered extras from the items list
    items[:] = [item for item in items if item not in extras_to_remove_global]
