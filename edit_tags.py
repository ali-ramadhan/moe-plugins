"""
Interactive tag editing plugin for Moe.

This plugin provides an interactive interface to edit or confirm matched tags on albums and tracks.
Users can navigate through album-level tags and individual track tags using arrow keys.
"""

import logging
import datetime
from typing import List, Optional, Any, Union, Dict, Set
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, VSplit, Window, ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.application import get_app
from prompt_toolkit.styles import Style
from prompt_toolkit.validation import Validator, ValidationError
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from sqlalchemy.orm.session import Session

import moe
import moe.config
from moe.library import Album, Track

__all__ = []

log = logging.getLogger("moe.edit_tags")


class DateValidator(Validator):
    """Validator for date fields."""

    def validate(self, document):
        """Validate date format."""
        text = document.text.strip()
        if not text:
            return  # Allow empty dates

        try:
            datetime.datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(message="Date must be in YYYY-MM-DD format")


class IntegerValidator(Validator):
    """Validator for integer fields."""

    def __init__(self, min_value: int = None, max_value: int = None):
        self.min_value = min_value
        self.max_value = max_value

    def validate(self, document):
        """Validate integer format and range."""
        text = document.text.strip()
        if not text:
            return  # Allow empty integers

        try:
            value = int(text)
            if self.min_value is not None and value < self.min_value:
                raise ValidationError(message=f"Value must be at least {self.min_value}")
            if self.max_value is not None and value > self.max_value:
                raise ValidationError(message=f"Value must be at most {self.max_value}")
        except ValueError:
            raise ValidationError(message="Must be a valid integer")


class SetValidator(Validator):
    """Validator for set fields (semicolon-separated values)."""

    def validate(self, document):
        """Validate set format."""
        text = document.text.strip()
        if not text:
            return  # Allow empty sets

        # Just verify it's a string - sets are created by splitting on semicolons
        # Individual items will be stripped of whitespace


class TagEditorInterface:
    """Interactive interface for editing album and track tags."""

    def __init__(self, album: Album):
        self.album = album
        self.tracks = sorted(album.tracks, key=lambda t: (t.disc, t.track_num))
        self.current_page = 0  # 0 = album, 1+ = tracks
        self.current_row = 0
        self.result = None
        self.changes_made = False
        self.editing_mode = False
        self.edit_buffer = Buffer()

        # Define editable fields for albums and tracks
        self.album_fields = [
            ("artist", "Artist", "text"),
            ("title", "Title", "text"),
            ("date", "Date", "date"),
            ("label", "Label", "text"),
            ("media", "Media", "text"),
            ("country", "Country", "text"),
            ("barcode", "Barcode", "text"),
            ("catalog_nums", "Catalog Numbers", "set"),
            ("genres", "Genres", "set"),
            ("disc_total", "Disc Total", "int"),
            ("track_total", "Track Total", "int"),
            ("original_date", "Original Date", "date"),
        ]

        self.track_fields = [
            ("artist", "Artist", "text"),
            ("title", "Title", "text"),
            ("track_num", "Track Number", "int"),
            ("disc", "Disc", "int"),
            ("composer", "Composer", "text"),
            ("composer_sort", "Composer Sort", "text"),
            ("artists", "Artists", "set"),
            ("genres", "Genres", "set"),
        ]

        # Store original values for comparison
        self.original_album_values = self._get_album_values()
        self.original_track_values = [self._get_track_values(track) for track in self.tracks]

    def _get_all_album_fields(self) -> Dict[str, Any]:
        """Get all album field values, including non-editable ones."""
        values = {}

        # Get editable fields first
        for field_name, _, _ in self.album_fields:
            value = getattr(self.album, field_name, None)
            if isinstance(value, set) and value:
                values[field_name] = ";".join(sorted(value))
            elif isinstance(value, datetime.date):
                values[field_name] = value.strftime("%Y-%m-%d")
            else:
                values[field_name] = str(value) if value is not None else ""

        # Get custom fields
        if hasattr(self.album, 'custom') and self.album.custom:
            for custom_field, custom_value in self.album.custom.items():
                if custom_field not in values:  # Don't override editable fields
                    if isinstance(custom_value, (list, set)):
                        values[custom_field] = ";".join(str(v) for v in custom_value) if custom_value else ""
                    else:
                        values[custom_field] = str(custom_value) if custom_value is not None else ""

        return values

    def _get_all_track_fields(self, track: Track) -> Dict[str, Any]:
        """Get all track field values, including non-editable ones."""
        values = {}

        # Get editable fields first
        for field_name, _, _ in self.track_fields:
            value = getattr(track, field_name, None)
            if isinstance(value, set) and value:
                values[field_name] = ";".join(sorted(value))
            else:
                values[field_name] = str(value) if value is not None else ""

        # Get custom fields
        if hasattr(track, 'custom') and track.custom:
            for custom_field, custom_value in track.custom.items():
                if custom_field not in values:  # Don't override editable fields
                    if isinstance(custom_value, (list, set)):
                        values[custom_field] = ";".join(str(v) for v in custom_value) if custom_value else ""
                    else:
                        values[custom_field] = str(custom_value) if custom_value is not None else ""

        return values

    def _get_readonly_fields(self) -> List[str]:
        """Get list of read-only field names for the current page."""
        if self.current_page == 0:
            all_fields = self._get_all_album_fields()
            editable_field_names = {field_name for field_name, _, _ in self.album_fields}
        else:
            track = self.tracks[self.current_page - 1]
            all_fields = self._get_all_track_fields(track)
            editable_field_names = {field_name for field_name, _, _ in self.track_fields}

        # Return fields that exist but are not editable
        readonly_fields = []
        for field_name, value in all_fields.items():
            if field_name not in editable_field_names and value.strip():
                readonly_fields.append(field_name)

        return sorted(readonly_fields)

    def _get_album_values(self) -> Dict[str, Any]:
        """Get current album field values."""
        values = {}
        for field_name, _, _ in self.album_fields:
            value = getattr(self.album, field_name, None)
            if isinstance(value, set) and value:
                values[field_name] = ";".join(sorted(value))
            elif isinstance(value, datetime.date):
                values[field_name] = value.strftime("%Y-%m-%d")
            else:
                values[field_name] = str(value) if value is not None else ""
        return values

    def _get_track_values(self, track: Track) -> Dict[str, Any]:
        """Get current track field values."""
        values = {}
        for field_name, _, _ in self.track_fields:
            value = getattr(track, field_name, None)
            if isinstance(value, set) and value:
                values[field_name] = ";".join(sorted(value))
            else:
                values[field_name] = str(value) if value is not None else ""
        return values

    def _set_album_value(self, field_name: str, value: str, field_type: str):
        """Set album field value with proper type conversion."""
        if not value.strip():
            setattr(self.album, field_name, None)
            return

        if field_type == "date":
            try:
                date_value = datetime.datetime.strptime(value.strip(), "%Y-%m-%d").date()
                setattr(self.album, field_name, date_value)
            except ValueError:
                pass  # Keep original value if invalid
        elif field_type == "int":
            try:
                int_value = int(value.strip())
                setattr(self.album, field_name, int_value)
            except ValueError:
                pass  # Keep original value if invalid
        elif field_type == "set":
            if value.strip():
                set_value = {item.strip() for item in value.split(";") if item.strip()}
                setattr(self.album, field_name, set_value)
            else:
                setattr(self.album, field_name, None)
        else:  # text
            setattr(self.album, field_name, value.strip())

    def _set_track_value(self, track: Track, field_name: str, value: str, field_type: str):
        """Set track field value with proper type conversion."""
        if not value.strip():
            if field_type == "int":
                # Don't allow empty values for required int fields
                if field_name in ["track_num", "disc"]:
                    return
            setattr(track, field_name, None)
            return

        if field_type == "int":
            try:
                int_value = int(value.strip())
                setattr(track, field_name, int_value)
            except ValueError:
                pass  # Keep original value if invalid
        elif field_type == "set":
            if value.strip():
                set_value = {item.strip() for item in value.split(";") if item.strip()}
                setattr(track, field_name, set_value)
            else:
                setattr(track, field_name, None)
        else:  # text
            setattr(track, field_name, value.strip())

    def get_page_title(self) -> str:
        """Get the title for the current page."""
        if self.current_page == 0:
            return f"Album: {self.album.artist} - {self.album.title}"
        else:
            track = self.tracks[self.current_page - 1]
            return f"Track {track.disc}.{track.track_num}: {track.artist} - {track.title}"

    def get_current_fields(self) -> List[tuple]:
        """Get the fields for the current page."""
        if self.current_page == 0:
            return self.album_fields
        else:
            return self.track_fields

    def get_current_values(self) -> Dict[str, Any]:
        """Get the current values for the current page."""
        if self.current_page == 0:
            return self._get_album_values()
        else:
            track = self.tracks[self.current_page - 1]
            return self._get_track_values(track)

    def get_formatted_text(self):
        """Generate the formatted text for the current state."""
        lines = []

        # Title
        title = self.get_page_title()
        lines.append(("class:title", f"🎵 {title}\n"))

        # Page indicator
        total_pages = len(self.tracks) + 1
        page_num = self.current_page + 1
        lines.append(("class:info", f"Page {page_num}/{total_pages} "))

        if self.current_page == 0:
            lines.append(("class:info", "(Album Tags)"))
        else:
            lines.append(("class:info", f"(Track {self.current_page} Tags)"))

        lines.append(("class:info", "\n\n"))

        # Help text
        if not self.editing_mode:
            lines.append(("class:help", "💡 Use ↑/↓ to navigate rows, ←/→ to navigate pages\n"))
            lines.append(("class:help", "   Enter = edit field, Space = confirm and continue, 'q' = quit\n"))
            lines.append(("class:help", "   Gray fields are read-only and show existing metadata\n\n"))

        # Editable Fields
        fields = self.get_current_fields()
        values = self.get_current_values()

        for i, (field_name, field_label, field_type) in enumerate(fields):
            value = values.get(field_name, "")

            # Determine if this field has been changed
            if self.current_page == 0:
                original_value = self.original_album_values.get(field_name, "")
            else:
                original_value = self.original_track_values[self.current_page - 1].get(field_name, "")

            changed = value != original_value

            # Format the display value
            display_value = value if value else "(empty)"

            # Apply styling based on current row and change state
            if i == self.current_row:
                if changed:
                    lines.append(("class:current_changed", f"❯ {field_label}: {display_value}\n"))
                else:
                    lines.append(("class:current", f"❯ {field_label}: {display_value}\n"))
            else:
                if changed:
                    lines.append(("class:changed", f"  {field_label}: {display_value}\n"))
                else:
                    lines.append(("class:normal", f"  {field_label}: {display_value}\n"))

        # Read-only fields section
        readonly_fields = self._get_readonly_fields()
        if readonly_fields:
            lines.append(("class:readonly_separator", "\n─── Read-only tags ───\n"))

            # Get all field values for the current page
            if self.current_page == 0:
                all_values = self._get_all_album_fields()
            else:
                track = self.tracks[self.current_page - 1]
                all_values = self._get_all_track_fields(track)

            for field_name in readonly_fields:
                value = all_values.get(field_name, "")
                display_value = value if value else "(empty)"

                # Format field name for display (capitalize and replace underscores)
                field_label = field_name.replace('_', ' ').title()

                lines.append(("class:readonly", f"  {field_label}: {display_value}\n"))

        # Summary of changes
        if self.changes_made:
            lines.append(("class:changes", "\n✨ Changes have been made to tags\n"))

        return FormattedText(lines)

    def get_edit_dialog_text(self):
        """Generate the formatted text for the edit dialog."""
        if not self.editing_mode:
            return FormattedText([])

        fields = self.get_current_fields()
        field_name, field_label, field_type = fields[self.current_row]

        lines = []
        lines.append(("class:dialog_title", f"✏️  Edit {field_label}\n\n"))

        # Add help text based on field type
        if field_type == "date":
            lines.append(("class:dialog_help", "Format: YYYY-MM-DD (e.g., 2023-12-25)\n"))
        elif field_type == "set":
            lines.append(("class:dialog_help", "Separate multiple values with ';' (e.g., Rock;Pop;Jazz)\n"))
        elif field_type == "int":
            lines.append(("class:dialog_help", "Enter a whole number\n"))
        else:
            lines.append(("class:dialog_help", "Enter text value\n"))

        lines.append(("class:dialog_help", "Press Enter to save, Escape to cancel\n\n"))

        return FormattedText(lines)

    def move_up(self):
        """Move to the previous row."""
        if not self.editing_mode and self.current_row > 0:
            self.current_row -= 1

    def move_down(self):
        """Move to the next row."""
        if not self.editing_mode:
            max_row = len(self.get_current_fields()) - 1
            if self.current_row < max_row:
                self.current_row += 1

    def move_left(self):
        """Move to the previous page with wrap-around."""
        if not self.editing_mode:
            if self.current_page > 0:
                self.current_page -= 1
            else:
                # Wrap to the last page (last track)
                self.current_page = len(self.tracks)
            self.current_row = 0

    def move_right(self):
        """Move to the next page with wrap-around."""
        if not self.editing_mode:
            max_page = len(self.tracks)
            if self.current_page < max_page:
                self.current_page += 1
            else:
                # Wrap to the first page (album)
                self.current_page = 0
            self.current_row = 0

    def start_edit(self):
        """Start editing the current field."""
        if self.editing_mode:
            return

        fields = self.get_current_fields()
        if self.current_row >= len(fields):
            return

        current_values = self.get_current_values()
        field_name, _, _ = fields[self.current_row]
        current_value = current_values.get(field_name, "")

        # Set up the edit buffer with current value
        self.edit_buffer.text = current_value
        self.editing_mode = True

    def cancel_edit(self):
        """Cancel the current edit."""
        self.editing_mode = False
        self.edit_buffer.text = ""

    def save_edit(self):
        """Save the current edit."""
        if not self.editing_mode:
            return

        fields = self.get_current_fields()
        field_name, field_label, field_type = fields[self.current_row]
        new_value = self.edit_buffer.text

        # Apply the new value
        try:
            if self.current_page == 0:
                self._set_album_value(field_name, new_value, field_type)
            else:
                track = self.tracks[self.current_page - 1]
                self._set_track_value(track, field_name, new_value, field_type)

            # Mark that changes have been made
            self.changes_made = True
        except Exception as e:
            log.error(f"Error setting field {field_name}: {e}")

        self.cancel_edit()

    def confirm_and_continue(self):
        """Confirm the current state and continue."""
        if not self.editing_mode:
            self.result = "confirmed"
            get_app().exit()

    def quit(self):
        """Quit without saving changes."""
        if self.editing_mode:
            self.cancel_edit()
        else:
            self.result = "quit"
            get_app().exit()


def create_tag_editor_interface(album: Album) -> str:
    """Create an interactive tag editor interface."""
    editor = TagEditorInterface(album)

    # Create key bindings
    kb = KeyBindings()

    # Condition for when we're in editing mode
    editing_condition = Condition(lambda: editor.editing_mode)
    not_editing_condition = Condition(lambda: not editor.editing_mode)

    @kb.add('up', filter=not_editing_condition)
    def move_up(event):
        editor.move_up()

    @kb.add('down', filter=not_editing_condition)
    def move_down(event):
        editor.move_down()

    @kb.add('left', filter=not_editing_condition)
    def move_left(event):
        editor.move_left()

    @kb.add('right', filter=not_editing_condition)
    def move_right(event):
        editor.move_right()

    @kb.add('enter')
    def handle_enter(event):
        if editor.editing_mode:
            editor.save_edit()
        else:
            editor.start_edit()

    @kb.add('escape')
    def handle_escape(event):
        if editor.editing_mode:
            editor.cancel_edit()

    @kb.add('space', filter=not_editing_condition)
    def confirm(event):
        editor.confirm_and_continue()

    @kb.add('q', filter=not_editing_condition)
    @kb.add('c-c')  # Ctrl+C should always work
    def quit(event):
        editor.quit()

    # Create the layout
    def get_main_content():
        return editor.get_formatted_text()

    def get_edit_content():
        return editor.get_edit_dialog_text()

    # Main window
    main_window = Window(
        content=FormattedTextControl(get_main_content),
        wrap_lines=True,
    )

    # Edit dialog window
    edit_dialog = Window(
        content=FormattedTextControl(get_edit_content),
        wrap_lines=True,
        height=4,  # Fixed height for the edit dialog
    )

    # Edit input window
    edit_input = Window(
        content=BufferControl(buffer=editor.edit_buffer),
        height=1,  # Single line input
    )

    # Layout with conditional edit dialog using ConditionalContainer
    layout = Layout(
        HSplit([
            main_window,
            # Show edit dialog when in editing mode using ConditionalContainer
            ConditionalContainer(
                HSplit([
                    edit_dialog,
                    edit_input,
                ]),
                filter=editing_condition
            ),
        ])
    )

    # Custom style
    style = Style.from_dict({
        'title': '#ansiblue bold',
        'info': '#ansicyan',
        'help': '#ansiyellow',
        'current': '#ansigreen bold',
        'current_changed': '#ansimagenta bold',
        'changed': '#ansimagenta',
        'normal': '',
        'changes': '#ansigreen bold',
        'dialog_title': '#ansiwhite bold',
        'dialog_help': '#ansiyellow',
        'readonly_separator': '#888888',
        'readonly': '#888888',
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
        return editor.result or "quit"
    except (KeyboardInterrupt, EOFError):
        return "quit"


@moe.hookimpl
def edit_new_items(session: Session, items):
    """Edit tags for new albums interactively."""
    # Group albums to process
    albums = [item for item in items if isinstance(item, Album)]

    if not albums:
        return

    def normalize_artist_string(artist_value: str) -> str:
        """Convert comma-separated artists to semicolon-separated with proper formatting."""
        if not artist_value or not isinstance(artist_value, str):
            return artist_value

        # Split on commas, strip whitespace, and rejoin with semicolons
        artists = [artist.strip() for artist in artist_value.split(',') if artist.strip()]
        return ';'.join(artists)

    def preprocess_artist_tags(album: Album):
        """Preprocess artist tags to convert commas to semicolons."""
        # Process album-level artist
        if hasattr(album, 'artist') and album.artist:
            album.artist = normalize_artist_string(album.artist)

        # Process track-level artist and artists fields
        for track in album.tracks:
            # Process single artist field
            if hasattr(track, 'artist') and track.artist:
                track.artist = normalize_artist_string(track.artist)

            # Process artists set field - need to handle if it contains comma-separated values
            if hasattr(track, 'artists') and track.artists:
                normalized_artists = set()
                for artist in track.artists:
                    if isinstance(artist, str) and ',' in artist:
                        # Split comma-separated artist and add individual artists
                        split_artists = [a.strip() for a in artist.split(',') if a.strip()]
                        normalized_artists.update(split_artists)
                    else:
                        normalized_artists.add(artist)
                track.artists = normalized_artists

    for album in albums:
        # Preprocess artist tags before showing the editor
        preprocess_artist_tags(album)

        # Show the interactive tag editor
        print(f"\n🎵 Editing tags for: {album.artist} - {album.title}")

        if len(album.tracks) > 0:
            print(f"   Album has {len(album.tracks)} track{'s' if len(album.tracks) != 1 else ''}")

        result = create_tag_editor_interface(album)

        if result == "confirmed":
            print("✅ Tags confirmed and saved")
        else:
            print("❌ Tag editing cancelled")
            # Note: We don't actually revert changes here as the user might want to keep some
            # The user would need to restart the import process to fully revert
