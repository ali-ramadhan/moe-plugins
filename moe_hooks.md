# Moe Plugin Hooks Reference

This document provides a comprehensive reference for all plugin hooks available in Moe. Hooks allow plugins to extend and customize Moe's behavior at various stages of operation.

## Table of Contents

1. [Configuration Hooks](#configuration-hooks)
2. [CLI Hooks](#cli-hooks)
3. [Library Item Lifecycle Hooks](#library-item-lifecycle-hooks)
4. [Import System Hooks](#import-system-hooks)
5. [Add System Hooks](#add-system-hooks)
6. [Move System Hooks](#move-system-hooks)
7. [Tag Reading and Writing Hooks](#tag-reading-and-writing-hooks)
8. [Duplicate Resolution Hooks](#duplicate-resolution-hooks)
9. [Uniqueness Check Hooks](#uniqueness-check-hooks)
10. [Plugin Registration Hooks](#plugin-registration-hooks)

---

## Configuration Hooks

### `add_config_validator`

**Module:** `moe.config`

**Purpose:** Add configuration validators for your plugin's settings.

**Arguments:**
- `settings` (`dynaconf.base.LazySettings`): Moe's settings object

**Usage:**
```python
@moe.hookimpl
def add_config_validator(settings):
    """Add configuration validators for my plugin."""
    settings.validators.register(
        dynaconf.Validator("MY_PLUGIN.SOME_SETTING", must_exist=True),
        dynaconf.Validator("MY_PLUGIN.ANOTHER_SETTING", default="default_value")
    )
```

**Description:** Use this hook to register configuration validators for your plugin. This ensures that your plugin's configuration is properly validated when Moe starts up.

---

## CLI Hooks

### `add_command`

**Module:** `moe.cli`

**Purpose:** Add sub-commands to Moe's CLI interface.

**Arguments:**
- `cmd_parsers` (`argparse._SubParsersAction`): The CLI sub-command parser collection

**Usage:**
```python
@moe.hookimpl
def add_command(cmd_parsers):
    """Add a custom command to Moe's CLI."""
    my_parser = cmd_parsers.add_parser(
        'mycommand',
        help='My custom command',
        description='Detailed description of my command'
    )
    my_parser.add_argument('--option', help='Some option')
    my_parser.set_defaults(func=my_command_function)

def my_command_function(session, args):
    """Function called when the command is executed."""
    # session: SQLAlchemy database session
    # args: Parsed command-line arguments
    pass
```

**Description:** This hook allows you to add custom commands to Moe's CLI. Your command function will receive a database session and the parsed arguments.

### `add_candidate_prompt_choice`

**Module:** `moe.moe_import.import_cli`

**Purpose:** Add custom choices to the import candidate selection prompt.

**Arguments:**
- `prompt_choices` (`list[PromptChoice]`): List of available prompt choices

**Usage:**
```python
@moe.hookimpl
def add_candidate_prompt_choice(prompt_choices):
    """Add a custom choice to the candidate prompt."""
    prompt_choices.append(
        PromptChoice(
            title="Custom Action",
            shortcut_key="c",
            func=my_custom_function
        )
    )

def my_custom_function(new_album, candidates):
    """Custom function executed when choice is selected."""
    pass
```

### `add_import_prompt_choice`

**Module:** `moe.moe_import.import_cli`

**Purpose:** Add custom choices to the import confirmation prompt.

**Arguments:**
- `prompt_choices` (`list[PromptChoice]`): List of available prompt choices

**Usage:**
```python
@moe.hookimpl
def add_import_prompt_choice(prompt_choices):
    """Add a custom choice to the import prompt."""
    prompt_choices.append(
        PromptChoice(
            title="Custom Import Action",
            shortcut_key="c",
            func=my_import_function
        )
    )

def my_import_function(new_album, candidate):
    """Custom function for processing import."""
    pass
```

---

## Library Item Lifecycle Hooks

### `edit_changed_items`

**Module:** `moe.library.lib_item`

**Purpose:** Edit items that have been modified in the current session before changes are committed.

**Arguments:**
- `session` (`Session`): Database session
- `items` (`list[LibItem]`): List of changed items

**Usage:**
```python
@moe.hookimpl
def edit_changed_items(session, items):
    """Edit changed items before they're finalized."""
    for item in items:
        if isinstance(item, Album):
            # Modify album fields
            item.custom['last_modified'] = datetime.now()
```

**Description:** This hook runs before changes are committed to the database, allowing you to modify items based on their changes.

### `edit_new_items`

**Module:** `moe.library.lib_item`

**Purpose:** Edit new items being added to the library before they're finalized.

**Arguments:**
- `session` (`Session`): Database session
- `items` (`list[LibItem]`): List of new items

**Usage:**
```python
@moe.hookimpl
def edit_new_items(session, items):
    """Edit new items before they're added to the library."""
    for item in items:
        if isinstance(item, Track):
            # Set default values or validate data
            if not item.genres:
                item.genres = {'Unknown'}
```

### `process_changed_items`

**Module:** `moe.library.lib_item`

**Purpose:** Process items after they've been changed and committed (read-only).

**Arguments:**
- `session` (`Session`): Database session
- `items` (`list[LibItem]`): List of changed items

**Usage:**
```python
@moe.hookimpl
def process_changed_items(session, items):
    """Process changed items after they're finalized."""
    for item in items:
        # Log changes, trigger external actions, etc.
        log.info(f"Item changed: {item}")
```

**Note:** Changes made to items in this hook are lost as they're already committed.

### `process_new_items`

**Module:** `moe.library.lib_item`

**Purpose:** Process new items after they've been added to the library (read-only).

**Arguments:**
- `session` (`Session`): Database session
- `items` (`list[LibItem]`): List of new items

**Usage:**
```python
@moe.hookimpl
def process_new_items(session, items):
    """Process new items after they're added."""
    for item in items:
        # Trigger post-add actions
        if isinstance(item, Album):
            compress_album_artwork(item)
```

### `process_removed_items`

**Module:** `moe.library.lib_item`

**Purpose:** Process items after they've been removed from the library.

**Arguments:**
- `session` (`Session`): Database session
- `items` (`list[LibItem]`): List of removed items

**Usage:**
```python
@moe.hookimpl
def process_removed_items(session, items):
    """Process removed items."""
    for item in items:
        # Clean up external resources
        cleanup_external_data(item)
```

---

## Import System Hooks

### `get_candidates`

**Module:** `moe.moe_import.import_core`

**Purpose:** Provide candidate albums from external metadata sources during import.

**Arguments:**
- `album` (`Album`): Album being imported

**Returns:** `list[CandidateAlbum]`

**Usage:**
```python
@moe.hookimpl
def get_candidates(album):
    """Get candidates from my metadata source."""
    candidates = []

    # Search external source
    search_results = my_api.search(album.artist, album.title)

    for result in search_results:
        meta_album = create_meta_album_from_result(result)
        match_value = calculate_match_score(album, meta_album)

        candidate = CandidateAlbum(
            album=meta_album,
            match_value=match_value,
            plugin_source="my_source",
            source_id=result.id,
            disambigs=[result.label, str(result.year)]
        )
        candidates.append(candidate)

    return candidates
```

### `process_candidates`

**Module:** `moe.moe_import.import_core`

**Purpose:** Process and apply candidate album metadata during import.

**Arguments:**
- `new_album` (`Album`): Album being added to library
- `candidates` (`list[CandidateAlbum]`): Sorted list of candidate albums

**Usage:**
```python
@moe.hookimpl
def process_candidates(new_album, candidates):
    """Process import candidates."""
    if candidates:
        # Automatically apply best match if confidence is high
        best_candidate = candidates[0]
        if best_candidate.match_value > 0.95:
            new_album.merge(best_candidate.album, overwrite=True)
```

---

## Add System Hooks

### `pre_add`

**Module:** `moe.add.add_core`

**Purpose:** Modify items before they're added to the library.

**Arguments:**
- `item` (`LibItem`): Item being added

**Usage:**
```python
@moe.hookimpl
def pre_add(item):
    """Modify items before adding to library."""
    if isinstance(item, Album):
        # Normalize album data
        item.title = item.title.strip()
        item.artist = normalize_artist_name(item.artist)
    elif isinstance(item, Track):
        # Validate track data
        if not item.title:
            item.title = f"Track {item.track_num}"
```

**Description:** This hook runs during the add process and allows you to modify items before they're added to the database.

---

## Move System Hooks

### `create_path_template_func`

**Module:** `moe.move.move_core`

**Purpose:** Provide custom functions for use in path templates.

**Returns:** `list[Callable]`

**Usage:**
```python
@moe.hookimpl
def create_path_template_func():
    """Create custom path template functions."""
    return [my_custom_function, another_function]

def my_custom_function(item):
    """Custom function for path templates."""
    # Return a string to be used in path templates
    # Can be called as {my_custom_function(album)} in templates
    return "custom_value"
```

### `override_album_path_config`

**Module:** `moe.move.move_core`

**Purpose:** Override the album path configuration based on album properties.

**Arguments:**
- `album` (`Album`): Album being moved

**Returns:** `Optional[str]` - New path template or None

**Usage:**
```python
@moe.hookimpl
def override_album_path_config(album):
    """Override path config for specific album types."""
    if "Classical" in album.title:
        return "Classical/{album.artist}/{album.title} ({album.year})"
    elif "Soundtrack" in album.title:
        return "Soundtracks/{album.title} ({album.year})"
    return None  # Use default config
```

---

## Tag Reading and Writing Hooks

### `read_custom_tags`

**Module:** `moe.library.track`

**Purpose:** Read custom tags from track files.

**Arguments:**
- `track_path` (`Path`): Path to the track file
- `album_fields` (`dict[str, Any]`): Album fields dictionary to populate
- `track_fields` (`dict[str, Any]`): Track fields dictionary to populate

**Usage:**
```python
@moe.hookimpl
def read_custom_tags(track_path, album_fields, track_fields):
    """Read custom tags from track files."""
    import mediafile

    audio_file = mediafile.MediaFile(track_path)

    # Read custom album fields
    if hasattr(audio_file, 'my_custom_field'):
        album_fields['my_custom_field'] = audio_file.my_custom_field

    # Read custom track fields
    if hasattr(audio_file, 'track_custom_field'):
        track_fields['track_custom_field'] = audio_file.track_custom_field
```

### `write_custom_tags`

**Module:** `moe.write`

**Purpose:** Write custom tags to track files.

**Arguments:**
- `track` (`Track`): Track to write tags to

**Usage:**
```python
@moe.hookimpl
def write_custom_tags(track):
    """Write custom tags to track files."""
    import mediafile

    audio_file = mediafile.MediaFile(track.path)

    # Write custom fields
    custom_value = track.custom.get('my_custom_field')
    if custom_value:
        audio_file.my_custom_field = custom_value

    audio_file.save()
```

---

## Duplicate Resolution Hooks

### `resolve_dup_items`

**Module:** `moe.duplicate.dup_core`

**Purpose:** Resolve conflicts between duplicate items.

**Arguments:**
- `session` (`Session`): Database session
- `item_a` (`LibItem`): First duplicate item
- `item_b` (`LibItem`): Second duplicate item

**Usage:**
```python
@moe.hookimpl
def resolve_dup_items(session, item_a, item_b):
    """Resolve duplicate items."""
    if isinstance(item_a, Album) and isinstance(item_b, Album):
        # Custom resolution logic
        if item_a.date and not item_b.date:
            # Keep item_a, remove item_b
            session.delete(item_b)
        elif item_b.date and not item_a.date:
            # Keep item_b, remove item_a
            session.delete(item_a)
```

---

## Uniqueness Check Hooks

### `is_unique_album`

**Module:** `moe.library.album`

**Purpose:** Add custom uniqueness criteria for albums.

**Arguments:**
- `album` (`Album`): First album
- `other` (`Album`): Second album to compare

**Returns:** `bool` - True if albums are unique, False if duplicates

**Usage:**
```python
@moe.hookimpl
def is_unique_album(album, other):
    """Add custom uniqueness check for albums."""
    # Consider albums with same custom field as duplicates
    my_field_a = album.custom.get('my_identifier')
    my_field_b = other.custom.get('my_identifier')

    if my_field_a and my_field_b and my_field_a == my_field_b:
        return False  # Not unique (duplicates)

    return True  # Unique
```

### `is_unique_track`

**Module:** `moe.library.track`

**Purpose:** Add custom uniqueness criteria for tracks.

**Arguments:**
- `track` (`Track`): First track
- `other` (`Track`): Second track to compare

**Returns:** `bool`

**Usage:**
```python
@moe.hookimpl
def is_unique_track(track, other):
    """Add custom uniqueness check for tracks."""
    # Add custom logic for track uniqueness
    if track.custom.get('isrc') == other.custom.get('isrc'):
        return False  # Same ISRC = duplicate
    return True
```

### `is_unique_extra`

**Module:** `moe.library.extra`

**Purpose:** Add custom uniqueness criteria for extra files.

**Arguments:**
- `extra` (`Extra`): First extra
- `other` (`Extra`): Second extra to compare

**Returns:** `bool`

**Usage:**
```python
@moe.hookimpl
def is_unique_extra(extra, other):
    """Add custom uniqueness check for extras."""
    # Custom logic for extra file uniqueness
    return True
```

---

## Plugin Registration Hooks

### `add_hooks`

**Module:** Various (config, cli, library modules, etc.)

**Purpose:** Register hook specifications for your plugin.

**Arguments:**
- `pm` (`pluggy._manager.PluginManager`): Plugin manager

**Usage:**
```python
@moe.hookimpl
def add_hooks(pm):
    """Register my plugin's hook specifications."""
    from my_plugin import Hooks
    pm.add_hookspecs(Hooks)
```

### `plugin_registration`

**Module:** `moe.config`

**Purpose:** Perform actions after initial plugin registration.

**Usage:**
```python
@moe.hookimpl
def plugin_registration():
    """Perform post-registration setup."""
    # Check dependencies
    if not config.CONFIG.pm.has_plugin("cli"):
        config.CONFIG.pm.set_blocked("my_plugin")
        log.warning("My plugin requires CLI plugin")

    # Register sub-modules
    if config.CONFIG.pm.has_plugin("move"):
        config.CONFIG.pm.register(my_submodule, "my_plugin_move")
```

### `register_sa_event_listeners`

**Module:** `moe.config`

**Purpose:** Register SQLAlchemy event listeners (internal use only).

**Usage:**
```python
@moe.hookimpl
def register_sa_event_listeners():
    """Register SQLAlchemy event listeners."""
    import sqlalchemy
    from moe.config import Session

    sqlalchemy.event.listen(
        Session,
        "before_flush",
        my_before_flush_handler
    )
```

**Note:** This hook is for internal Moe use and should generally not be used by external plugins.

---

## Hook Implementation Guidelines

### Basic Hook Implementation

All hooks are implemented using the `@moe.hookimpl` decorator:

```python
import moe

@moe.hookimpl
def my_hook_implementation(arg1, arg2):
    """My hook implementation."""
    # Hook logic here
    pass
```

### Hook Execution Order

You can control when your hook runs relative to others:

```python
@moe.hookimpl(tryfirst=True)
def early_hook():
    """Runs before other implementations."""
    pass

@moe.hookimpl(trylast=True)
def late_hook():
    """Runs after other implementations."""
    pass
```

### Hook Wrappers

For advanced use cases, you can create hook wrappers:

```python
@moe.hookimpl(hookwrapper=True)
def wrapper_hook():
    """Wrap around other hook implementations."""
    # Code before other hooks
    result = yield  # Run other hook implementations
    # Code after other hooks
    return result
```

### Error Handling

Always handle errors gracefully in hooks:

```python
@moe.hookimpl
def safe_hook():
    """Hook with proper error handling."""
    try:
        # Hook logic
        pass
    except Exception as e:
        log.error(f"Error in my hook: {e}")
        # Don't re-raise unless necessary
```

---

## Complete Plugin Example

Here's a simple complete plugin that demonstrates several hooks:

```python
"""Example Moe plugin."""

import logging
import moe
from moe import config

log = logging.getLogger("my_plugin")

@moe.hookimpl
def add_config_validator(settings):
    """Add configuration for my plugin."""
    import dynaconf
    settings.validators.register(
        dynaconf.Validator("MY_PLUGIN.ENABLED", default=True)
    )

@moe.hookimpl
def add_command(cmd_parsers):
    """Add custom command."""
    parser = cmd_parsers.add_parser("hello", help="Say hello")
    parser.set_defaults(func=hello_command)

def hello_command(session, args):
    """Hello command implementation."""
    print("Hello from my plugin!")

@moe.hookimpl
def edit_new_items(session, items):
    """Add custom field to new albums."""
    if not config.CONFIG.settings.my_plugin.enabled:
        return

    for item in items:
        if isinstance(item, Album):
            item.custom['processed_by_my_plugin'] = True

@moe.hookimpl
def write_custom_tags(track):
    """Write custom tags."""
    import mediafile

    if track.album.custom.get('processed_by_my_plugin'):
        audio_file = mediafile.MediaFile(track.path)
        audio_file.comment = "Processed by My Plugin"
        audio_file.save()
```

This documentation covers all the major hooks available in Moe's plugin system. Each hook serves a specific purpose in the music library management workflow, allowing plugins to extend and customize Moe's behavior at various stages.
