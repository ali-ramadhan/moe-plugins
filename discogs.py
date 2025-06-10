"""
Discogs API plugin for Moe.

Provides Discogs as a metadata source for music tagging.

Heavily based on the beets Discogs plugin: https://github.com/beetbox/beets/blob/master/beetsplug/discogs.py

See Also:
    * https://www.discogs.com/developers/
    * https://python3-discogs-client.readthedocs.io/
"""

import datetime
import logging
import re
from pathlib import Path
from typing import Any, Optional

import dynaconf.base
import moe
from moe import config
from moe.library import Album, LibItem, MetaAlbum, MetaTrack
from moe.moe_import import CandidateAlbum
from moe.util.core import match
from sqlalchemy.orm.session import Session

try:
    import discogs_client
    from discogs_client.exceptions import DiscogsAPIError
except ImportError:
    discogs_client = None
    DiscogsAPIError = Exception

__all__ = [
    "DiscogsError",
    "get_album_by_id",
    "get_candidate_by_id",
]

log = logging.getLogger("moe.discogs")

USER_AGENT = "moe/2.1.3 +https://mrmoe.readthedocs.io/"


class DiscogsError(Exception):
    """Discogs API error."""


# Global client instance
_discogs_client = None


def _get_discogs_client():
    """Get or create a Discogs client instance."""
    global _discogs_client

    if _discogs_client is None:
        if discogs_client is None:
            raise DiscogsError(
                "discogs_client library not installed. Install with: pip install python3-discogs-client"
            )

        try:
            user_token = config.CONFIG.settings.discogs.user_token
        except (AttributeError, KeyError):
            raise DiscogsError(
                "Discogs user token not configured. Set 'discogs.user_token' in your config."
            )

        _discogs_client = discogs_client.Client(USER_AGENT, user_token=user_token)

    return _discogs_client


@moe.hookimpl
def add_config_validator(settings: dynaconf.base.LazySettings):
    """Validates discogs plugin configuration settings."""
    settings.validators.register(  # type: ignore
        dynaconf.Validator("discogs.user_token", must_exist=True)
    )
    settings.validators.register(  # type: ignore
        dynaconf.Validator("discogs.search_limit", default=5)
    )


@moe.hookimpl
def get_candidates(album: Album) -> list[CandidateAlbum]:
    """Get candidate albums from Discogs.

    Args:
        album: Original album used to search Discogs for a matching album.

    Returns:
        List of candidate albums from Discogs.
    """
    log.debug(f"Getting candidate albums from Discogs. [{album=!r}]")

    try:
        client = _get_discogs_client()
    except DiscogsError as err:
        log.error(f"Failed to initialize Discogs client: {err}")
        return []

    # Build search query
    query_parts = []
    if album.artist:
        query_parts.append(album.artist)
    if album.title:
        query_parts.append(album.title)

    if not query_parts:
        log.warning("No artist or title to search for")
        return []

    query = " ".join(query_parts)

    # Clean up query - remove special characters that might break search
    query = re.sub(r"(?u)\W+", " ", query)
    query = re.sub(r"(?i)\b(CD|disc|vinyl)\s*\d+", "", query)
    query = query.strip()

    if not query:
        log.warning("Query became empty after cleaning")
        return []

    try:
        try:
            search_limit = config.CONFIG.settings.discogs.search_limit
        except (AttributeError, KeyError):
            search_limit = 5

        log.debug(f"Searching Discogs for: {query}")

        print(f"Searching Discogs for: {query}")
        results = client.search(query, type="release")
        results.per_page = search_limit
        releases = results.page(1)

        candidates = []
        for release in releases:
            try:
                candidate = get_candidate_by_id(album, str(release.id))
                if candidate:
                    candidates.append(candidate)
            except Exception as e:
                log.warning(f"Failed to process release {release.id}: {e}")
                continue

        if not candidates:
            log.warning("No candidate albums found.")
        else:
            log.info(f"Found {len(candidates)} candidate albums.")

        return candidates

    except Exception as e:
        log.error(f"Error searching Discogs: {e}")
        return []


@moe.hookimpl
def read_custom_tags(
    track_path: Path, album_fields: dict[str, Any], track_fields: dict[str, Any]
) -> None:
    """Read Discogs IDs from track file tags."""
    try:
        import mediafile
        audio_file = mediafile.MediaFile(track_path)

        # Try to read Discogs release ID from various possible tag fields
        discogs_id = None
        for field in ["discogs_release_id", "discogs_id"]:
            if hasattr(audio_file, field):
                discogs_id = getattr(audio_file, field)
                if discogs_id:
                    break

        if discogs_id:
            album_fields["discogs_release_id"] = discogs_id

    except ImportError:
        log.debug("mediafile not available for reading custom tags")
    except Exception as e:
        log.debug(f"Error reading custom tags: {e}")


@moe.hookimpl
def write_custom_tags(track):
    """Write Discogs ID fields as tags."""
    if not track:
        log.warning("Track is None, cannot write custom tags")
        return

    if not track.album:
        log.warning("Track album is None, cannot write Discogs tags")
        return

    try:
        import mediafile
        audio_file = mediafile.MediaFile(track.path)

        discogs_id = track.album.custom.get("discogs_release_id")
        if discogs_id:
            # Write to a custom field - mediafile might not have direct Discogs support
            audio_file.discogs_release_id = discogs_id

        audio_file.save()

    except ImportError:
        log.debug("mediafile not available for writing custom tags")
    except Exception as e:
        log.debug(f"Error writing custom tags: {e}")


def get_album_by_id(release_id: str) -> MetaAlbum:
    """Returns an album from Discogs with the given release ID."""
    log.debug(f"Fetching release from Discogs. [release={release_id!r}]")

    try:
        client = _get_discogs_client()
        release = discogs_client.Release(client, {"id": int(release_id)})

        # Force fetch the release data
        print(f"Fetching Discogs release ID: {release_id}")
        release.refresh()

        log.info(f"Fetched release from Discogs. [release={release_id!r}]")
        return _create_album(release)

    except Exception as e:
        log.error(f"Error fetching album {release_id}: {e}")
        raise DiscogsError(f"Failed to fetch album {release_id}") from e


def get_candidate_by_id(album: Album, release_id: str) -> Optional[CandidateAlbum]:
    """Returns a candidate for album from the given release_id."""
    try:
        log.debug(f"Fetching release from Discogs. [release={release_id!r}]")

        candidate_album = get_album_by_id(release_id)

        # Calculate match value
        match_value = match.get_match_value(album, candidate_album)

        # Build disambiguations
        disambigs = []
        if candidate_album.date:
            disambigs.append(str(candidate_album.date.year))
        if candidate_album.country:
            disambigs.append(candidate_album.country)
        if candidate_album.label:
            disambigs.append(candidate_album.label)

        return CandidateAlbum(
            album=candidate_album,
            match_value=match_value,
            plugin_source="discogs",
            source_id=release_id,
            disambigs=disambigs,
        )

    except Exception as e:
        log.error(f"Error creating candidate for release {release_id}: {e}")
        return None


def _safe_get(obj, key: str, default=None):
    """Safely get a value from an object that might be a dict or have attributes.

    Args:
        obj: Object that might be a dictionary or an object with attributes.
        key: Key/attribute name to access.
        default: Default value if key/attribute doesn't exist.

    Returns:
        Value from the object or default.
    """
    if obj is None:
        return default

    # Try dictionary access first
    if hasattr(obj, 'get') and callable(obj.get):
        return obj.get(key, default)

    # Try attribute access
    if hasattr(obj, key):
        return getattr(obj, key, default)

    # Try dictionary-style access (for dict-like objects without .get())
    if hasattr(obj, '__getitem__'):
        try:
            return obj[key]
        except (KeyError, TypeError):
            pass

    return default


def _safe_has(obj, key: str) -> bool:
    """Safely check if an object has a key/attribute.

    Args:
        obj: Object that might be a dictionary or an object with attributes.
        key: Key/attribute name to check.

    Returns:
        True if key/attribute exists, False otherwise.
    """
    if obj is None:
        return False

    # Try dictionary-style check
    if hasattr(obj, '__contains__'):
        try:
            return key in obj
        except TypeError:
            pass

    # Try attribute check
    return hasattr(obj, key)


def _get_release_date(release) -> Optional[datetime.date]:
    """Gets the release date from a Discogs release.

    Args:
        release: Discogs release object.

    Returns:
        Release date if available, otherwise original date.
    """
    # Use the specific release year
    if hasattr(release, 'year') and release.year:
        return datetime.date(release.year, 1, 1)

    # Fall back to original date if no specific release year
    return _get_original_date(release)


def _get_original_date(release) -> Optional[datetime.date]:
    """Gets the original release date from a Discogs release's master.

    Args:
        release: Discogs release object.

    Returns:
        Original release date if available from master release.
    """
    # Try to get the master release year
    if hasattr(release, 'master') and release.master:
        try:
            master = release.master
            if hasattr(master, 'year') and master.year:
                return datetime.date(master.year, 1, 1)
        except Exception:
            # If we can't access the master, continue
            pass

    # If no master or master year, use the release year as original
    if hasattr(release, 'year') and release.year:
        return datetime.date(release.year, 1, 1)

    return None


def _create_album(release) -> MetaAlbum:
    """Creates a MetaAlbum from a Discogs release."""
    log.debug(f"Creating album from Discogs release. [release={release.id!r}]")

    # Get basic info
    title = release.title

    # Get artist info
    artist = _get_artist_name(release.artists)

    # Get release date and original date (like MusicBrainz plugin)
    release_date = _get_release_date(release)
    original_date = _get_original_date(release)

    # Get label and catalog info
    label = None
    catalog_nums = set()
    if hasattr(release, 'labels') and release.labels:
        label_info = release.labels[0]
        if hasattr(label_info, 'name'):
            label = label_info.name
        if hasattr(label_info, 'catno') and label_info.catno and label_info.catno != "none":
            catalog_nums.add(label_info.catno)

    # Get country
    country = release.country if hasattr(release, 'country') else None

    # Get format/media info
    media = None
    if hasattr(release, 'formats') and release.formats:
        # Use _safe_get to handle both dict and object formats
        media = _safe_get(release.formats[0], 'name', None)

    # Get genre/style
    genres = []
    if hasattr(release, 'genres') and release.genres:
        genres.extend(release.genres)
    if hasattr(release, 'styles') and release.styles:
        genres.extend(release.styles)
    genre = ", ".join(genres) if genres else None

    # Create the album with both dates (like MusicBrainz plugin)
    album = MetaAlbum(
        artist=artist,
        title=title,
        date=release_date,
        original_date=original_date,
        country=country,
        label=label,
        catalog_nums=catalog_nums or set(),
        media=media,
        genre=genre,
    )

    # Store discogs_release_id in custom fields after creation
    album.custom['discogs_release_id'] = str(release.id)

    # Get tracks
    if hasattr(release, 'tracklist') and release.tracklist:
        tracks = _create_tracks(release.tracklist, album)
        album.tracks.extend(tracks)
        album.track_total = len(tracks)

        # Set disc total based on tracks
        disc_nums = {track.disc for track in tracks if track.disc}
        album.disc_total = max(disc_nums) if disc_nums else 1

    log.debug(f"Created album from Discogs release. [{album=!r}]")
    return album


def _get_artist_name(artists) -> str:
    """Extract artist name from Discogs artist list."""
    if not artists:
        return ""

    # Handle both artist objects and simple strings
    if hasattr(artists[0], 'name'):
        return artists[0].name
    elif isinstance(artists[0], dict) and 'name' in artists[0]:
        return artists[0]['name']
    else:
        return str(artists[0])


def _create_tracks(tracklist, album: MetaAlbum) -> list[MetaTrack]:
    """Create MetaTrack objects from Discogs tracklist."""
    tracks = []

    for i, track_data in enumerate(tracklist, 1):
        # Get basic track info using safe access
        title = _safe_get(track_data, 'title', f'Track {i}')
        position = _safe_get(track_data, 'position', str(i))

        # Parse position to get disc and track numbers
        disc, track_num = _parse_track_position(position, i)

        # Get track artist (might be different from album artist)
        track_artist = album.artist  # Default to album artist
        if _safe_has(track_data, 'artists'):
            track_artists = _safe_get(track_data, 'artists')
            if track_artists:
                track_artist = _get_artist_name(track_artists)

        # Get duration if available
        duration = None
        if _safe_has(track_data, 'duration'):
            duration_str = _safe_get(track_data, 'duration')
            if duration_str:
                duration = _parse_duration(duration_str)

        track = MetaTrack(
            album=album,
            title=title,
            artist=track_artist,
            track_num=track_num,
            disc=disc,
            length=duration,
        )
        tracks.append(track)

    return tracks


def _parse_track_position(position: str, fallback_num: int) -> tuple[int, int]:
    """Parse Discogs track position to disc and track numbers."""
    if not position:
        return 1, fallback_num

    # Handle formats like "A1", "B2", "1-1", "2.1", etc.
    position = position.upper().strip()

    # Check for letter format first (A1, B2, etc.)
    if position and position[0].isalpha():
        disc = ord(position[0]) - ord('A') + 1
        track_nums = re.findall(r'\d+', position)
        track_num = int(track_nums[0]) if track_nums else fallback_num
        return disc, track_num

    # Try to extract numbers for numeric formats
    numbers = re.findall(r'\d+', position)

    if len(numbers) >= 2:
        # Format like "1-1" or "2.1"
        return int(numbers[0]), int(numbers[1])
    elif len(numbers) == 1:
        # Single number, assume disc 1
        return 1, int(numbers[0])
    else:
        # Fallback
        return 1, fallback_num


def _parse_duration(duration_str: str) -> Optional[int]:
    """Parse duration string to seconds."""
    if not duration_str:
        return None

    try:
        # Handle formats like "3:45" or "1:23:45"
        parts = duration_str.split(':')
        if len(parts) == 2:
            # MM:SS
            minutes, seconds = parts
            return int(minutes) * 60 + int(seconds)
        elif len(parts) == 3:
            # HH:MM:SS
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    except (ValueError, TypeError):
        pass

    return None
