"""Album Type Classification Plugin for Moe.

This plugin allows users to interactively categorize albums into different types
(regular album, compilation, soundtrack, classical music) and automatically
organizes them into appropriate directory structures.
"""

import logging
from typing import Optional

import dynaconf.base
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.application import get_app
from prompt_toolkit.styles import Style

import moe
from moe import config
from moe.library import Album

__all__ = ["classify_album_type"]

log = logging.getLogger("moe.album_type")

# Album type constants
ALBUM_TYPE_REGULAR = "regular"
ALBUM_TYPE_COMPILATION = "compilation"
ALBUM_TYPE_SOUNDTRACK = "soundtrack"
ALBUM_TYPE_CLASSICAL = "classical"


@moe.hookimpl
def add_config_validator(settings: dynaconf.base.LazySettings):
    """Add configuration validators for album_type plugin."""
    import dynaconf

    # No defaults needed - if not specified, albums use the default album_path
    settings.validators.register(
        dynaconf.Validator("ALBUM_TYPE.COMPILATION_ALBUM_PATH", default=None),
        dynaconf.Validator("ALBUM_TYPE.SOUNDTRACK_ALBUM_PATH", default=None),
        dynaconf.Validator("ALBUM_TYPE.CLASSICAL_ALBUM_PATH", default=None),
    )


def _get_path_template(album_type: str) -> Optional[str]:
    """Get the path template for a given album type from config."""
    if album_type == ALBUM_TYPE_COMPILATION:
        return config.CONFIG.settings.get("album_type.compilation_album_path")
    elif album_type == ALBUM_TYPE_SOUNDTRACK:
        return config.CONFIG.settings.get("album_type.soundtrack_album_path")
    elif album_type == ALBUM_TYPE_CLASSICAL:
        return config.CONFIG.settings.get("album_type.classical_album_path")
    else:
        # Regular albums and any other types use the default album_path
        return None


@moe.hookimpl
def override_album_path_config(album: Album) -> Optional[str]:
    """Override album path based on album type classification."""
    # Just use the stored classification - prompting happens in edit_new_items
    album_type = album.custom.get("album_type")
    if album_type:
        return _get_path_template(album_type)
    return None


def _auto_detect_album_type(album: Album) -> Optional[str]:
    """Attempt to automatically detect album type based on metadata."""
    title_lower = album.title.lower() if album.title else ""
    artist_lower = album.artist.lower() if album.artist else ""

    # Check for soundtrack indicators
    soundtrack_keywords = [
        "soundtrack", "score", "original motion picture", "ost", "theme from",
        "music from", "songs from", "film score", "movie soundtrack"
    ]
    if any(keyword in title_lower for keyword in soundtrack_keywords):
        return ALBUM_TYPE_SOUNDTRACK

    # Check for classical music indicators
    classical_keywords = [
        "symphony", "concerto", "sonata", "quartet", "quintet", "opera",
        "requiem", "mass", "cantata", "oratorio", "fugue", "prelude",
        "nocturne", "etude", "waltz", "polonaise", "mazurka"
    ]
    classical_composers = [
        "bach", "mozart", "beethoven", "chopin", "tchaikovsky", "vivaldi",
        "brahms", "schubert", "debussy", "stravinsky", "rachmaninoff",
        "handel", "haydn", "liszt", "wagner", "mahler", "dvorak"
    ]

    if (any(keyword in title_lower for keyword in classical_keywords) or
        any(composer in artist_lower for composer in classical_composers)):
        return ALBUM_TYPE_CLASSICAL

    # Check for compilation indicators
    compilation_keywords = [
        "compilation", "greatest hits", "best of", "collection", "anthology",
        "various artists", "va -", "mixed by", "selected by", "compiled by"
    ]
    if (any(keyword in title_lower for keyword in compilation_keywords) or
        any(keyword in artist_lower for keyword in compilation_keywords)):
        return ALBUM_TYPE_COMPILATION

    # If no clear indicators, return None (will prompt user if enabled)
    return None


def _prompt_for_album_type(album: Album, default_choice: Optional[str] = None) -> Optional[str]:
    """Prompt the user to classify the album type."""
    try:
        # Show album info first
        print(f"\n🎵 Album Classification Required")
        print(f"Artist: {album.artist}")
        print(f"Title: {album.title}")
        if album.date:
            print(f"Year: {album.date.year}")

        # Show auto-detection result if available
        if default_choice:
            type_labels = {
                ALBUM_TYPE_REGULAR: "regular album",
                ALBUM_TYPE_COMPILATION: "compilation",
                ALBUM_TYPE_SOUNDTRACK: "soundtrack",
                ALBUM_TYPE_CLASSICAL: "classical music"
            }
            suggested_label = type_labels.get(default_choice, "unknown")
            print(f"💡 Auto-detected as: {suggested_label}")

        print("-" * 50)

        # Create the album type selector
        choice_data = [
            (ALBUM_TYPE_REGULAR, "🎶 Regular Album"),
            (ALBUM_TYPE_COMPILATION, "📀 Compilation/Various Artists"),
            (ALBUM_TYPE_SOUNDTRACK, "🎬 Soundtrack/Score"),
            (ALBUM_TYPE_CLASSICAL, "🎼 Classical Music"),
            (None, "❓ Skip classification"),
        ]

        selected_type = _create_album_type_selector(
            "How would you like to categorize this album?",
            choice_data,
            default_choice
        )

        if selected_type:
            type_labels = {
                ALBUM_TYPE_REGULAR: "regular album",
                ALBUM_TYPE_COMPILATION: "compilation",
                ALBUM_TYPE_SOUNDTRACK: "soundtrack",
                ALBUM_TYPE_CLASSICAL: "classical music"
            }
            print(f"✅ Classified as: {type_labels.get(selected_type, 'unknown')}")

        return selected_type

    except (KeyboardInterrupt, EOFError):
        print("\n⏭️  Skipping album classification.")
        return None
    except Exception as e:
        log.error(f"Error during album type prompting: {e}")
        return None


def _create_album_type_selector(prompt_text: str, choice_data: list, default_choice: Optional[str] = None) -> Optional[str]:
    """Create an interactive album type selector using prompt_toolkit."""

    class AlbumTypeSelector:
        def __init__(self, prompt_text: str, choice_data: list, default_choice: Optional[str] = None):
            self.prompt_text = prompt_text
            self.choice_data = choice_data  # List of (value, display_text) tuples
            self.selected_index = 0
            self.result = None

            # Set default selection
            if default_choice:
                for i, (value, _) in enumerate(choice_data):
                    if value == default_choice:
                        self.selected_index = i
                        break

        def get_formatted_text(self):
            """Generate the formatted text for the current state."""
            lines = [
                ("class:question", f"{self.prompt_text}\n"),
                ("class:info", "💡 Use ↑/↓ to navigate, Enter to select, 'q' to quit\n\n"),
            ]

            for i, (value, display_text) in enumerate(self.choice_data):
                if i == self.selected_index:
                    if value is None:  # Skip option
                        lines.append(("class:selected_skip", f"❯ {display_text}\n"))
                    else:
                        lines.append(("class:selected", f"❯ {display_text}\n"))
                else:
                    lines.append(("class:normal", f"  {display_text}\n"))

            return FormattedText(lines)

        def move_up(self):
            if self.selected_index > 0:
                self.selected_index -= 1

        def move_down(self):
            if self.selected_index < len(self.choice_data) - 1:
                self.selected_index += 1

        def select_current(self):
            value, _ = self.choice_data[self.selected_index]
            self.result = value
            get_app().exit()

        def quit(self):
            self.result = None
            get_app().exit()

    if not choice_data:
        return None

    selector = AlbumTypeSelector(prompt_text, choice_data, default_choice)

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

    # Create and run the application
    app = Application(
        layout=layout,
        key_bindings=kb,
        style=_get_prompt_style(),
        full_screen=False,
        mouse_support=False,
    )

    try:
        app.run()
        return selector.result
    except (KeyboardInterrupt, EOFError):
        return None


def _get_prompt_style():
    """Get the style for prompt_toolkit components."""
    return Style.from_dict({
        'question': '#ansiblue bold',
        'info': '#ansiyellow',
        'selected': '#ansigreen bold',
        'selected_skip': '#ansired bold',
        'normal': '',
    })


def classify_album_type(album: Album, album_type: str) -> None:
    """Manually classify an album type (for use by other plugins)."""
    valid_types = [ALBUM_TYPE_REGULAR, ALBUM_TYPE_COMPILATION, ALBUM_TYPE_SOUNDTRACK, ALBUM_TYPE_CLASSICAL]
    if album_type not in valid_types:
        raise ValueError(f"Invalid album type: {album_type}. Valid types: {valid_types}")

    album.custom["album_type"] = album_type
    log.info(f"Manually classified album as '{album_type}': {album.artist} - {album.title}")


@moe.hookimpl
def edit_new_items(session, items):
    """Classify new albums being added to the library."""
    albums = [item for item in items if isinstance(item, Album)]

    for album in albums:
        # Skip if already classified
        if "album_type" in album.custom:
            continue

        # Auto-detect to suggest a default
        suggested_type = _auto_detect_album_type(album)

        # Always prompt the user with auto-detected suggestion as default
        album_type = _prompt_for_album_type(album, default_choice=suggested_type)

        # Store the classification
        if album_type:
            album.custom["album_type"] = album_type
            log.info(f"Classified album as '{album_type}': {album.artist} - {album.title}")
        else:
            # If user skipped, default to regular
            album.custom["album_type"] = ALBUM_TYPE_REGULAR
            log.debug(f"Defaulted to regular album: {album.artist} - {album.title}")
