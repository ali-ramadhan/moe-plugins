"""
Filter extra files plugin for Moe.

This plugin provides an interactive interface to filter and categorize extra files during import.
Users can select which extra files to keep using checkboxes and categorize them for proper organization.
"""

import logging
import subprocess
import re
from collections import defaultdict
from typing import List, Set, Optional
from pathlib import Path

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

# Categorization constants
CATEGORY_COVER = "cover"
CATEGORY_ARTWORK = "artwork"
CATEGORY_CUE_LOG = "cue_log"


@moe.hookimpl
def add_config_validator(settings):
    """Add configuration validators for organize_extras_interactive plugin."""
    import dynaconf

    validators = [
        dynaconf.Validator("FILTER_EXTRAS.TEXT_EDITOR", default=None),
        dynaconf.Validator("FILTER_EXTRAS.EXCLUDE_BY_DEFAULT", default=[], cast=list),
    ]
    settings.validators.register(*validators)


def _is_cue_log_file(extra: Extra) -> bool:
    """Check if a file is a CUE or log file based on extension."""
    cue_log_extensions = {'.cue', '.log'}
    return extra.path.suffix.lower() in cue_log_extensions


def _should_exclude_by_default(extra: Extra) -> bool:
    """Check if a file should be excluded by default based on configuration."""
    try:
        exclude_extensions = moe.config.CONFIG.settings.filter_extras.exclude_by_default
        if not exclude_extensions:
            return False

        # Normalize extensions (ensure they start with a dot)
        normalized_extensions = []
        for ext in exclude_extensions:
            if not ext.startswith('.'):
                ext = '.' + ext
            normalized_extensions.append(ext.lower())

        return extra.path.suffix.lower() in normalized_extensions
    except (AttributeError, KeyError):
        return False


def _get_safe_display_path(extra: Extra) -> str:
    """Get display path for an extra, handling cases where it's outside the album directory."""
    try:
        return str(extra.rel_path)
    except ValueError:
        # Extra is outside album directory (e.g., downloaded cover in temp dir)
        return extra.path.name


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
        display_path = _get_safe_display_path(extra)
        return f"{display_path} ({size_str})"
    except (OSError, AttributeError):
        display_path = _get_safe_display_path(extra)
        return str(display_path)


class ExtrasFilterSelector:
    """Interactive selector for filtering and categorizing extra files."""

    def __init__(self, extras: List[Extra], album: Album):
        self.extras = extras
        self.album = album
        self.selected_extras: Set[int] = set()  # Start empty, will populate based on rules
        self.current_index = 0
        self.result = None

        # Categorization state - maps extra index to category
        self.categories = {}  # int -> str

        # Track which extra is the cover (only one allowed)
        self.cover_index = None

        # Apply default selection and categorization rules
        self._apply_default_rules()

    def _apply_default_rules(self):
        """Apply default selection and categorization rules."""
        potential_cover_index = None

        for i, extra in enumerate(self.extras):
            # Auto-detect and categorize CUE/log files
            if _is_cue_log_file(extra):
                self.categories[i] = CATEGORY_CUE_LOG
                self.selected_extras.add(i)  # CUE/log files are selected by default
            # Check if file should be excluded by default
            elif _should_exclude_by_default(extra):
                # Don't add to selected_extras (excluded by default)
                pass
            else:
                # All other files are selected by default and need categorization
                self.selected_extras.add(i)
                # Keep track of potential cover files (image files)
                if potential_cover_index is None and self._is_image_file(extra):
                    potential_cover_index = i
                # Auto-categorize as artwork if not already categorized
                if i not in self.categories:
                    self.categories[i] = CATEGORY_ARTWORK

        # Auto-assign the first image file as cover if we found one
        if potential_cover_index is not None:
            self.cover_index = potential_cover_index
            self.categories[potential_cover_index] = CATEGORY_COVER

    def _is_image_file(self, extra: Extra) -> bool:
        """Check if a file is an image based on extension."""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
        return extra.path.suffix.lower() in image_extensions

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
            help_text = "💡 Use ↑/↓ to navigate, Space to toggle, Enter to confirm\n" \
                       "   'c' = set as cover, 'a' = set as artwork, 'u' = set as cue/log\n" \
                       "   'r' = remove category, 'o' = open file, 't' = text editor, 'q' = quit\n" \
                       "   Note: All selected files must be categorized and exactly 1 cover required!\n\n"
        else:
            help_text = "💡 Use ↑/↓ to navigate, Space to toggle, Enter to confirm\n" \
                       "   'c' = set as cover, 'a' = set as artwork, 'u' = set as cue/log\n" \
                       "   'r' = remove category, 'o' = open file, 'q' = quit\n" \
                       "   Note: All selected files must be categorized and exactly 1 cover required!\n\n"

        lines = [
            ("class:title", f"Filter and categorize extra files for: {artist} - {title}\n"),
            ("class:info", help_text),
        ]

        for i, extra in enumerate(self.extras):
            file_info = get_file_info(extra)
            checkbox = "☑" if i in self.selected_extras else "☐"

            # Add category indicator
            category_indicator = ""
            if i in self.categories:
                if self.categories[i] == CATEGORY_COVER:
                    category_indicator = " (📸 Cover)"
                elif self.categories[i] == CATEGORY_ARTWORK:
                    category_indicator = " (🎨 Artwork)"
                elif self.categories[i] == CATEGORY_CUE_LOG:
                    category_indicator = " (💿 CUE/Log)"
            elif i in self.selected_extras:
                # Selected but uncategorized - show warning
                category_indicator = " ⚠️ (Needs category!)"

            display_text = f"{checkbox} {file_info}{category_indicator}"

            # Determine the style based on selection and current position
            if i == self.current_index:
                if i in self.selected_extras:
                    lines.append(("class:selected", f"❯ {display_text}\n"))
                else:
                    lines.append(("class:selected_dimmed", f"❯ {display_text}\n"))
            else:
                if i in self.selected_extras:
                    lines.append(("class:normal", f"  {display_text}\n"))
                else:
                    lines.append(("class:dimmed", f"  {display_text}\n"))

        selected_count = len(self.selected_extras)
        total_count = len(self.extras)

        # Count categorized files
        cover_count = 1 if self.cover_index is not None else 0
        artwork_count = sum(1 for cat in self.categories.values() if cat == CATEGORY_ARTWORK)
        cue_log_count = sum(1 for cat in self.categories.values() if cat == CATEGORY_CUE_LOG)

        # Count uncategorized selected files
        uncategorized_count = len([i for i in self.selected_extras if i not in self.categories])

        lines.append(("class:summary", f"\nSelected: {selected_count}/{total_count} files\n"))
        lines.append(("class:summary", f"Categorized: Cover({cover_count}), Artwork({artwork_count}), CUE/Log({cue_log_count})\n"))

        # Show validation warnings
        warnings = []
        if uncategorized_count > 0:
            warnings.append(f"{uncategorized_count} selected file(s) need categorization")
        if cover_count == 0:
            warnings.append("exactly 1 cover file must be selected")
        elif cover_count > 1:  # This shouldn't happen with current logic, but just in case
            warnings.append("only 1 cover file allowed")

        if warnings:
            lines.append(("class:warning", f"⚠️  {', '.join(warnings).capitalize()}!\n"))

        return FormattedText(lines)

    def move_up(self):
        if self.current_index > 0:
            self.current_index -= 1

    def move_down(self):
        if self.current_index < len(self.extras) - 1:
            self.current_index += 1

    def toggle_current(self):
        if self.current_index in self.selected_extras:
            # Deselecting - remove from selected and categories
            self.selected_extras.remove(self.current_index)
            if self.current_index in self.categories:
                if self.cover_index == self.current_index:
                    self.cover_index = None
                del self.categories[self.current_index]
        else:
            # Selecting - add to selected and require categorization
            self.selected_extras.add(self.current_index)
            # Auto-assign as artwork if not already categorized
            if self.current_index not in self.categories:
                self.categories[self.current_index] = CATEGORY_ARTWORK

    def select_all(self):
        self.selected_extras = set(range(len(self.extras)))
        # Ensure all selected files have categories
        for i in self.selected_extras:
            if i not in self.categories:
                self.categories[i] = CATEGORY_ARTWORK

    def select_none(self):
        self.selected_extras.clear()
        # Clear all categories since nothing is selected
        self.categories.clear()
        self.cover_index = None

    def set_as_cover(self):
        """Set the current file as the cover (only one allowed)."""
        if self.current_index < len(self.extras):
            # Remove previous cover designation
            if self.cover_index is not None:
                self.categories.pop(self.cover_index, None)

            # Set new cover
            self.cover_index = self.current_index
            self.categories[self.current_index] = CATEGORY_COVER

            # Ensure it's selected
            self.selected_extras.add(self.current_index)

    def set_as_artwork(self):
        """Set the current file as artwork."""
        if self.current_index < len(self.extras):
            # Remove from cover if it was set
            if self.cover_index == self.current_index:
                self.cover_index = None

            self.categories[self.current_index] = CATEGORY_ARTWORK
            # Ensure it's selected
            self.selected_extras.add(self.current_index)

    def set_as_cue_log(self):
        """Set the current file as CUE/log file."""
        if self.current_index < len(self.extras):
            # Remove from cover if it was set
            if self.cover_index == self.current_index:
                self.cover_index = None

            self.categories[self.current_index] = CATEGORY_CUE_LOG
            # Ensure it's selected
            self.selected_extras.add(self.current_index)

    def remove_category(self):
        """Remove category from the current file and deselect it."""
        if self.current_index in self.categories:
            if self.cover_index == self.current_index:
                self.cover_index = None
            del self.categories[self.current_index]
            # Also deselect the file since it no longer has a category
            self.selected_extras.discard(self.current_index)

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

    def can_confirm(self):
        """Check if the current state allows confirmation (all selected files are categorized and exactly one cover)."""
        all_categorized = all(i in self.categories for i in self.selected_extras)
        has_exactly_one_cover = self.cover_index is not None and self.cover_index in self.selected_extras
        return all_categorized and has_exactly_one_cover

    def confirm_selection(self):
        # Validate that all selected files are categorized
        if not self.can_confirm():
            return  # Don't exit if validation fails

        # Store categorization info in extras' custom fields
        for i, extra in enumerate(self.extras):
            if i in self.categories:
                extra.custom["filter_extras_category"] = self.categories[i]
            else:
                # Remove any existing categorization
                extra.custom.pop("filter_extras_category", None)

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

    @kb.add('c')
    def set_cover(event):
        selector.set_as_cover()

    @kb.add('a')
    def set_artwork(event):
        selector.set_as_artwork()

    @kb.add('u')
    def set_cue_log(event):
        selector.set_as_cue_log()

    @kb.add('r')
    def remove_category(event):
        selector.remove_category()

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
        if selector.can_confirm():
            selector.confirm_selection()
        else:
            # Flash a message or beep to indicate that confirmation is blocked
            # For now, we'll just do nothing and let the user see the warning in the display
            pass

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
        'selected_dimmed': '#888888 bold',  # Gray but bold for current item when deselected
        'normal': '',
        'dimmed': '#888888',  # Gray for deselected items
        'summary': '#ansicyan',
        'warning': '#ansired bold',  # Red for warnings
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


def _generate_cue_log_path(extra: Extra) -> str:
    """Generate path for CUE/log files based on organize_extras.py logic."""
    album = extra.album
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


def _get_extra_destination_path(extra: Extra) -> str:
    """Get the destination path for an extra file based on its categorization."""
    category = extra.custom.get("filter_extras_category")

    if category == CATEGORY_COVER:
        # Cover files go to root with album title
        ext = extra.path.suffix
        return f"{extra.album.title}{ext}"

    elif category == CATEGORY_ARTWORK:
        # Artwork files go into Artwork/ directory, keep original name
        return f"Artwork/{extra.path.name}"

    elif category == CATEGORY_CUE_LOG:
        # CUE/log files use organize_extras.py logic
        return _generate_cue_log_path(extra)

    # For uncategorized files, use the original name (default behavior)
    return extra.path.name


@moe.hookimpl
def override_extra_path_config(extra: Extra) -> Optional[str]:
    """Override extra path configuration based on categorization."""
    category = extra.custom.get("filter_extras_category")

    if category == CATEGORY_COVER:
        # Cover files go to root with album title
        ext = extra.path.suffix
        return f"{extra.album.title}{ext}"

    elif category == CATEGORY_ARTWORK:
        # Artwork files go into Artwork/ directory, keep original name
        return f"Artwork/{extra.path.name}"

    elif category == CATEGORY_CUE_LOG:
        # CUE/log files use organize_extras.py logic
        return _generate_cue_log_path(extra)

    # For uncategorized files, use default behavior
    return None


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
        # Always show the filtering interface, regardless of extra count
        album = album_id_to_album[album_obj_id]
        print(f"\n📁 Found {len(extras)} extra file{'s' if len(extras) != 1 else ''} for: {album.artist} - {album.title}")

        # Show the interactive filter interface
        selected_extras = create_extras_filter_interface(extras, album)

        # Identify extras to remove
        extras_to_remove = [extra for extra in extras if extra not in selected_extras]

        for extra in extras_to_remove:
            log.debug(f"Removing extra: {_get_safe_display_path(extra)}")
            album.extras.remove(extra)
            extras_to_remove_global.append(extra)

        if extras_to_remove:
            removed_count = len(extras_to_remove)
            kept_count = len(selected_extras)
            print(f"✅ Kept {kept_count} extra file{'s' if kept_count != 1 else ''}, removed {removed_count} extra file{'s' if removed_count != 1 else ''}")

            # Show which files were kept with their categories and destination paths
            for extra in selected_extras:
                category = extra.custom.get("filter_extras_category")
                dest_path = _get_extra_destination_path(extra)
                display_path = _get_safe_display_path(extra)

                if category == CATEGORY_COVER:
                    print(f"   📸 {display_path} → {dest_path} (Cover)")
                elif category == CATEGORY_ARTWORK:
                    print(f"   🎨 {display_path} → {dest_path} (Artwork)")
                elif category == CATEGORY_CUE_LOG:
                    print(f"   💿 {display_path} → {dest_path} (CUE/Log)")
                else:
                    print(f"   ✅ {display_path} → {dest_path}")
        else:
            print("✅ All extra files kept")

    # Remove the filtered extras from the items list
    items[:] = [item for item in items if item not in extras_to_remove_global]
