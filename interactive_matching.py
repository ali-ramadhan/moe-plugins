"""
Interactive Track Matching plugin for Moe.

This plugin provides an interactive interface to manually correct track matches during import.
When MusicBrainz (or other sources) provide candidate albums, users can interactively review
and correct the automatic track matching to ensure proper metadata assignment.
"""

import logging
from typing import List, Optional, Dict, Tuple, Set
from dataclasses import dataclass

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.application import get_app
from prompt_toolkit.styles import Style

import moe
from moe.library import Album, MetaTrack
from moe.moe_import import CandidateAlbum
from moe.util.core import get_matching_tracks, get_match_value
from moe.util.cli import PromptChoice

__all__ = []

log = logging.getLogger("moe.interactive_matching")
# Disable debug logging for clean UI
# log.setLevel(logging.DEBUG)

# Custom type for track matches: (original_track, candidate_track, match_score)
TrackMatchInfo = Tuple[Optional[MetaTrack], Optional[MetaTrack], float]


@dataclass
class TrackMatchPair:
    """Represents a manual track match assignment."""
    original_track: Optional[MetaTrack]
    candidate_track: Optional[MetaTrack]
    match_score: float
    is_manual: bool = False
    is_auto: bool = True


class InteractiveTrackMatcher:
    """Interactive interface for correcting track matches."""

    def __init__(self, original_album: Album, candidate_album: Album):
        self.original_album = original_album
        self.candidate_album = candidate_album
        self.result = None

        # Get sorted track lists - keep them in order always
        self.original_tracks = sorted(original_album.tracks, key=lambda t: (t.disc, t.track_num))
        self.candidate_tracks = sorted(candidate_album.tracks, key=lambda t: (t.disc, t.track_num))

        # Get initial automatic matches
        auto_matches = get_matching_tracks(original_album, candidate_album)

        # Convert to our internal representation - keep tracks in order
        self.matches: List[TrackMatchPair] = []
        self._build_match_pairs_ordered(auto_matches)

        # UI state
        self.current_row = 0
        self.selected_side = "original"  # "original" or "candidate"
        self.show_help = True

        # Locking state for two-step matching
        self.locked_original_track = None
        self.locked_candidate_track = None
        self.locked_side = None  # Which side was locked first

        # Track which tracks are already matched to prevent double-matching
        self.matched_original_indices: Set[int] = set()
        self.matched_candidate_indices: Set[int] = set()
        self._update_matched_indices()

    def _build_match_pairs_ordered(self, auto_matches):
        """Build match pairs but keep tracks in their original order."""
        # Only create match pairs for actual matches
        for orig_track, cand_track in auto_matches:
            if orig_track is not None and cand_track is not None:
                match_score = get_match_value(orig_track, cand_track)
                self.matches.append(TrackMatchPair(
                    original_track=orig_track,
                    candidate_track=cand_track,
                    match_score=match_score,
                    is_auto=True
                ))

        # Note: We don't create match pairs for unmatched tracks anymore
        # The display logic now shows all tracks directly from original_tracks and candidate_tracks
        # and looks up matches in the matches list

    def _get_current_side_for_row(self, row_index):
        """Get which side is currently selected for a matched pair row."""
        if hasattr(self, '_row_side_preferences') and row_index in self._row_side_preferences:
            return self._row_side_preferences[row_index]
        return "original"  # Default to original side

    def _get_current_track(self):
        """Get the track on the currently selected row."""
        if not hasattr(self, '_current_all_rows'):
            # Build the rows if not cached
            self._build_all_rows()

        if self.current_row >= len(self._current_all_rows):
            return None

        row = self._current_all_rows[self.current_row]

        if row['type'] == 'matched_pair':
            # For matched pairs, return the track on the currently selected side
            current_side = self._get_current_side_for_row(self.current_row)
            if current_side == "original":
                return row['original_track']
            else:
                return row['candidate_track']
        else:
            # For unmatched tracks
            return row['track']

    def _get_current_side(self):
        """Get which side (original or candidate) the current row is on."""
        if not hasattr(self, '_current_all_rows'):
            self._build_all_rows()

        if self.current_row >= len(self._current_all_rows):
            return "original"

        row = self._current_all_rows[self.current_row]

        if row['type'] == 'matched_pair':
            return self._get_current_side_for_row(self.current_row)
        else:
            return row['side']

    def _build_all_rows(self):
        """Build the current rows structure for navigation."""
        self._current_all_rows = []

        # Add matched pairs
        for match in self.matches:
            if match.original_track and match.candidate_track:
                self._current_all_rows.append({
                    'type': 'matched_pair',
                    'original_track': match.original_track,
                    'candidate_track': match.candidate_track,
                    'match_score': match.match_score,
                    'is_manual': match.is_manual
                })

        # Add unmatched original tracks
        for orig_track in self.original_tracks:
            is_matched = any(match.original_track == orig_track for match in self.matches if match.candidate_track)
            if not is_matched:
                self._current_all_rows.append({
                    'type': 'unmatched_original',
                    'track': orig_track,
                    'side': 'original'
                })

        # Add unmatched candidate tracks
        for cand_track in self.candidate_tracks:
            is_matched = any(match.candidate_track == cand_track for match in self.matches if match.original_track)
            if not is_matched:
                self._current_all_rows.append({
                    'type': 'unmatched_candidate',
                    'track': cand_track,
                    'side': 'candidate'
                })

    def _get_track_row_index(self, track, side):
        """Find which row contains the given track on the given side."""
        if side == "original":
            try:
                return self.original_tracks.index(track)
            except ValueError:
                return None
        else:  # candidate
            try:
                return len(self.original_tracks) + self.candidate_tracks.index(track)
            except ValueError:
                return None

    def _get_track_display(self, track: MetaTrack) -> str:
        """Get display string for a track."""
        if track is None:
            return "(no match)"

        # Format: "1.01 Artist - Title (3:45)"
        disc_track = f"{track.disc}.{track.track_num:02d}"
        artist = track.artist or "Unknown Artist"
        title = track.title or "Unknown Title"

        # Try to get duration if available
        duration = ""
        if hasattr(track, 'length') and track.length:
            minutes = int(track.length // 60)
            seconds = int(track.length % 60)
            duration = f" ({minutes}:{seconds:02d})"
        elif hasattr(track, 'path') and track.path:
            # Try to get duration from file metadata
            try:
                import mediafile
                audio_file = mediafile.MediaFile(track.path)
                if audio_file.length:
                    minutes = int(audio_file.length // 60)
                    seconds = int(audio_file.length % 60)
                    duration = f" ({minutes}:{seconds:02d})"
            except:
                pass

        return f"{disc_track} {artist} - {title}{duration}"

    def _get_max_column_width(self) -> tuple[int, int]:
        """Calculate the maximum width needed for each column."""
        max_orig_width = 0
        max_cand_width = 0

        for match in self.matches:
            if match.original_track:
                orig_display = self._get_track_display(match.original_track)
                max_orig_width = max(max_orig_width, len(orig_display))

            if match.candidate_track:
                cand_display = self._get_track_display(match.candidate_track)
                max_cand_width = max(max_cand_width, len(cand_display))

        # Add some padding and ensure minimum widths
        max_orig_width = max(max_orig_width + 2, 40)
        max_cand_width = max(max_cand_width + 2, 40)

        return max_orig_width, max_cand_width

    def _get_match_display_style(self, match: TrackMatchPair, is_current: bool) -> str:
        """Get the display style for a match based on its properties."""
        if is_current:
            if match.original_track and match.candidate_track:
                if match.match_score >= 0.8:
                    return "class:current_good_match"
                elif match.match_score >= 0.5:
                    return "class:current_ok_match"
                else:
                    return "class:current_poor_match"
            else:
                return "class:current_unmatched"
        else:
            if match.original_track and match.candidate_track:
                if match.match_score >= 0.8:
                    return "class:good_match"
                elif match.match_score >= 0.5:
                    return "class:ok_match"
                else:
                    return "class:poor_match"
            else:
                return "class:unmatched"

    def get_formatted_text(self):
        """Generate the formatted text for the current state."""
        lines = []

        # Title
        orig_title = f"{self.original_album.artist} - {self.original_album.title}"
        cand_title = f"{self.candidate_album.artist} - {self.candidate_album.title}"
        lines.append(("class:title", f"🎵 Interactive Track Matching\n"))
        lines.append(("class:info", f"Original: {orig_title}\n"))
        lines.append(("class:info", f"Candidate: {cand_title}\n\n"))

        if self.show_help:
            lines.append(("class:help", "💡 Navigation: ↑/↓ = move rows, ←/→ = switch sides\n"))
            lines.append(("class:help", "   Step 1: Space/m = lock track (turns orange)\n"))
            lines.append(("class:help", "   Step 2: Navigate to other side and Space/m = match tracks\n"))
            lines.append(("class:help", "   'u' = unmatch, 'r' = reset, 'h' = toggle help, Enter = confirm, Ctrl+C = exit\n\n"))

        # Calculate column widths
        orig_width, cand_width = self._get_max_column_width()

        # Column headers with dynamic width
        lines.append(("class:header", f"{'Original Tracks':<{orig_width}} {'Match':<8} {'Candidate Tracks':<{cand_width}}\n"))
        lines.append(("class:header", "─" * orig_width + " " + "─" * 8 + " " + "─" * cand_width + "\n"))

        # Build row list: matched pairs first, then unmatched tracks
        all_rows = []

        # Add matched pairs (both tracks on same row)
        for match in self.matches:
            if match.original_track and match.candidate_track:
                all_rows.append({
                    'type': 'matched_pair',
                    'original_track': match.original_track,
                    'candidate_track': match.candidate_track,
                    'match_score': match.match_score,
                    'is_manual': match.is_manual
                })

        # Add unmatched original tracks
        for orig_track in self.original_tracks:
            # Check if this track is already matched
            is_matched = any(match.original_track == orig_track for match in self.matches if match.candidate_track)
            if not is_matched:
                all_rows.append({
                    'type': 'unmatched_original',
                    'track': orig_track,
                    'side': 'original'
                })

        # Add unmatched candidate tracks
        for cand_track in self.candidate_tracks:
            # Check if this track is already matched
            is_matched = any(match.candidate_track == cand_track for match in self.matches if match.original_track)
            if not is_matched:
                all_rows.append({
                    'type': 'unmatched_candidate',
                    'track': cand_track,
                    'side': 'candidate'
                })

        # Display each row
        for i, row in enumerate(all_rows):
            is_current = (i == self.current_row)

            if row['type'] == 'matched_pair':
                # Matched pair - show both tracks on same row
                orig_track = row['original_track']
                cand_track = row['candidate_track']
                match_score = row['match_score']
                is_manual = row['is_manual']

                # Determine which side is selected for this row
                current_side = self._get_current_side_for_row(i)

                # Get track displays
                orig_display = self._get_track_display(orig_track)
                cand_display = self._get_track_display(cand_track)

                # Truncate if needed
                if len(orig_display) > orig_width - 2:
                    orig_display = orig_display[:orig_width-4] + ".."
                if len(cand_display) > cand_width - 2:
                    cand_display = cand_display[:cand_width-4] + ".."

                # Match info
                match_info = f"{match_score:.0%}"
                if is_manual:
                    match_info += "🔧"
                else:
                    match_info += "🤖"

                # Determine styling and cursors
                if is_current:
                    style = "class:current_selected"
                    if current_side == "original":
                        orig_cursor = "❯ "
                        cand_cursor = "  "
                    else:
                        orig_cursor = "  "
                        cand_cursor = "❯ "
                else:
                    if match_score >= 0.8:
                        style = "class:good_match"
                    elif match_score >= 0.5:
                        style = "class:ok_match"
                    else:
                        style = "class:poor_match"
                    orig_cursor = "  "
                    cand_cursor = "  "

                # Check for locked tracks
                if orig_track == self.locked_original_track:
                    orig_cursor = "🔒 "
                    style = "class:locked_track"
                elif cand_track == self.locked_candidate_track:
                    cand_cursor = "🔒 "
                    style = "class:locked_track"

                orig_part = f"{orig_cursor}{orig_display:<{orig_width-2}}"
                match_part = f" {match_info:^8} "
                cand_part = f"{cand_cursor}{cand_display:<{cand_width-2}}"

            else:
                # Unmatched track
                track = row['track']
                side = row['side']

                # Get track display
                track_display = self._get_track_display(track)

                # Determine styling and cursor
                if is_current:
                    style = "class:current_selected"
                    cursor = "❯ "
                else:
                    style = "class:unmatched"
                    cursor = "  "

                # Check for locked tracks
                if (side == 'original' and track == self.locked_original_track) or \
                   (side == 'candidate' and track == self.locked_candidate_track):
                    cursor = "🔒 "
                    style = "class:locked_track"

                if side == 'original':
                    # Unmatched original track
                    if len(track_display) > orig_width - 2:
                        track_display = track_display[:orig_width-4] + ".."

                    orig_part = f"{cursor}{track_display:<{orig_width-2}}"
                    match_part = f" {'─':^8} "
                    cand_part = f"  {'':<{cand_width-2}}"
                else:
                    # Unmatched candidate track
                    if len(track_display) > cand_width - 2:
                        track_display = track_display[:cand_width-4] + ".."

                    orig_part = f"  {'':<{orig_width-2}}"
                    match_part = f" {'─':^8} "
                    cand_part = f"{cursor}{track_display:<{cand_width-2}}"

            # Add the row
            lines.append((style, orig_part))
            lines.append(("class:match_info", match_part))
            lines.append((style, cand_part + "\n"))

        # Summary statistics
        matched_count = len([m for m in self.matches if m.original_track and m.candidate_track])
        manual_matches = sum(1 for m in self.matches if m.is_manual and m.original_track and m.candidate_track)
        unmatched_count = len(self.original_tracks) + len(self.candidate_tracks) - (matched_count * 2)

        lines.append(("class:summary", f"\n📊 {matched_count} matches ({manual_matches} manual), {unmatched_count} unmatched\n"))

        # Current selection and locking info
        if self.current_row < len(all_rows):
            current_row_info = all_rows[self.current_row]

            if current_row_info['type'] == 'matched_pair':
                # For matched pairs, get the currently selected side
                side = self._get_current_side_for_row(self.current_row)
            else:
                # For unmatched tracks, get the side from the row
                side = current_row_info['side']

            if self.locked_original_track or self.locked_candidate_track:
                locked_side = "original" if self.locked_original_track else "candidate"
                other_side = "candidate" if locked_side == "original" else "original"
                lines.append(("class:selection_info", f"🔒 {locked_side.title()} track locked - navigate to {other_side} side to match\n"))
            else:
                lines.append(("class:selection_info", f"Selected: {side} side - press Space/m to lock track\n"))

        return FormattedText(lines)

    def move_up(self):
        """Move to the previous row."""
        if self.current_row > 0:
            self.current_row -= 1

    def move_down(self):
        """Move to the next row."""
        self._build_all_rows()  # Ensure we have current rows
        if self.current_row < len(self._current_all_rows) - 1:
            self.current_row += 1

    def switch_side(self):
        """Switch between original and candidate sides."""
        self._build_all_rows()
        if self.current_row >= len(self._current_all_rows):
            return

        row = self._current_all_rows[self.current_row]
        if row['type'] == 'matched_pair':
            # For matched pairs, toggle between sides
            current_side = self._get_current_side_for_row(self.current_row)
            # Store the side preference (this is a simple implementation)
            if not hasattr(self, '_row_side_preferences'):
                self._row_side_preferences = {}

            if current_side == "original":
                self._row_side_preferences[self.current_row] = "candidate"
            else:
                self._row_side_preferences[self.current_row] = "original"
        else:
            # For unmatched tracks, try to find a matching row on the other side
            if row['side'] == 'original':
                # Look for unmatched candidate tracks
                for i, other_row in enumerate(self._current_all_rows):
                    if other_row['type'] == 'unmatched_candidate':
                        self.current_row = i
                        break
            else:
                # Look for unmatched original tracks
                for i, other_row in enumerate(self._current_all_rows):
                    if other_row['type'] == 'unmatched_original':
                        self.current_row = i
                        break

    def toggle_help(self):
        """Toggle help display."""
        self.show_help = not self.show_help

    def toggle_match(self):
        """Toggle match status or lock/unlock tracks for two-step matching."""
        current_track = self._get_current_track()
        current_side = self._get_current_side()

        if not current_track:
            return

        # Check if we're clicking on a locked track to unlock it
        if (current_side == "original" and current_track == self.locked_original_track) or \
           (current_side == "candidate" and current_track == self.locked_candidate_track):
            # Unlock the track
            self._unlock_tracks()
            return

        # If no tracks are locked, lock the current track
        if not self.locked_original_track and not self.locked_candidate_track:
            self._lock_current_track()
        else:
            # We have a locked track, try to match with current track
            self._complete_match()

    def _lock_current_track(self):
        """Lock the current track for matching."""
        current_track = self._get_current_track()
        current_side = self._get_current_side()

        if not current_track:
            return

        if current_side == "original":
            self.locked_original_track = current_track
            self.locked_side = "original"
        else:
            self.locked_candidate_track = current_track
            self.locked_side = "candidate"

    def _unlock_tracks(self):
        """Unlock all locked tracks."""
        self.locked_original_track = None
        self.locked_candidate_track = None
        self.locked_side = None

    def _complete_match(self):
        """Complete the match between locked track and current track."""
        current_track = self._get_current_track()
        current_side = self._get_current_side()

        if not current_track:
            return

        # Determine which tracks to match
        if self.locked_side == "original" and current_side == "candidate":
            # Original locked, selecting candidate
            orig_track = self.locked_original_track
            cand_track = current_track
        elif self.locked_side == "candidate" and current_side == "original":
            # Candidate locked, selecting original
            orig_track = current_track
            cand_track = self.locked_candidate_track
        else:
            # Same side selected, just lock the new track
            self._unlock_tracks()
            self._lock_current_track()
            return

        # Create the match
        self._create_match(orig_track, cand_track)
        self._unlock_tracks()

    def _create_match(self, orig_track, cand_track):
        """Create a match between original and candidate tracks."""
        # Remove any existing matches for these tracks
        self._remove_existing_matches(orig_track, cand_track)

        # Calculate match score
        match_score = get_match_value(orig_track, cand_track)

        # Add the new match
        new_match = TrackMatchPair(
            original_track=orig_track,
            candidate_track=cand_track,
            match_score=match_score,
            is_manual=True,
            is_auto=False
        )

        # Insert in the right position to maintain order
        self.matches.append(new_match)
        self._sort_matches()
        self._update_matched_indices()
        self._invalidate_row_cache()

    def _remove_existing_matches(self, orig_track, cand_track):
        """Remove any existing matches involving these tracks."""
        self.matches = [
            match for match in self.matches
            if not (match.original_track == orig_track or
                   match.candidate_track == cand_track or
                   (match.original_track == orig_track and match.candidate_track is None) or
                   (match.candidate_track == cand_track and match.original_track is None))
        ]

    def _sort_matches(self):
        """Sort matches to maintain track order."""
        # Separate matched and unmatched
        matched = [m for m in self.matches if m.original_track and m.candidate_track]
        unmatched_orig = [m for m in self.matches if m.original_track and not m.candidate_track]
        unmatched_cand = [m for m in self.matches if m.candidate_track and not m.original_track]

        # Sort each group
        matched.sort(key=lambda m: (m.original_track.disc, m.original_track.track_num))
        unmatched_orig.sort(key=lambda m: (m.original_track.disc, m.original_track.track_num))
        unmatched_cand.sort(key=lambda m: (m.candidate_track.disc, m.candidate_track.track_num))

        # Rebuild matches list
        self.matches = matched + unmatched_orig + unmatched_cand

    def unmatch_current(self):
        """Unmatch the current track if it's matched."""
        current_track = self._get_current_track()
        if not current_track:
            return

        # Find and remove any match involving this track
        for i, match in enumerate(self.matches):
            if match.original_track == current_track or match.candidate_track == current_track:
                # Only unmatch if both tracks are present (it's actually matched)
                if match.original_track and match.candidate_track:
                    self.matches.pop(i)
                    self._update_matched_indices()
                    self._invalidate_row_cache()

                    # Clear any locked tracks since we just unmatched
                    self._unlock_tracks()
                    break

    def _update_matched_indices(self):
        """Update the sets of matched track indices."""
        self.matched_original_indices.clear()
        self.matched_candidate_indices.clear()

        for i, match in enumerate(self.matches):
            if match.original_track:
                try:
                    idx = self.original_tracks.index(match.original_track)
                    self.matched_original_indices.add(idx)
                except ValueError:
                    pass
            if match.candidate_track:
                try:
                    idx = self.candidate_tracks.index(match.candidate_track)
                    self.matched_candidate_indices.add(idx)
                except ValueError:
                    pass

    def reset_to_auto(self):
        """Reset all matches to automatic matching."""
        auto_matches = get_matching_tracks(self.original_album, self.candidate_album)
        self.matches.clear()
        self._build_match_pairs_ordered(auto_matches)
        self._update_matched_indices()
        self._invalidate_row_cache()
        self._build_all_rows()  # Rebuild rows
        self.current_row = min(self.current_row, len(self._current_all_rows) - 1)
        self._unlock_tracks()

    def confirm_matches(self):
        """Confirm the current matches and exit."""
        self.result = "confirmed"
        get_app().exit()

    def cancel_matching(self):
        """Cancel matching and exit without changes."""
        self.result = "cancelled"
        get_app().exit()

    def apply_matches_to_albums(self):
        """Apply the corrected matches back to the original album."""
        if self.result != "confirmed":
            return

        # Copy album-level metadata from candidate to original album
        self._copy_album_metadata()

        # Clear existing tracks from original album
        self.original_album.tracks.clear()

        # Apply matched tracks with corrected metadata
        for match in self.matches:
            if match.original_track and match.candidate_track:
                # Copy metadata from candidate to original track
                orig_track = match.original_track
                cand_track = match.candidate_track

                # Update metadata while preserving file path and other local info
                orig_track.artist = cand_track.artist
                orig_track.title = cand_track.title
                orig_track.track_num = cand_track.track_num
                orig_track.disc = cand_track.disc

                # Copy other metadata fields
                if hasattr(cand_track, 'mb_track_id'):
                    orig_track.custom['mb_track_id'] = cand_track.custom.get('mb_track_id')
                if hasattr(cand_track, 'artists') and cand_track.artists:
                    orig_track.artists = cand_track.artists
                if hasattr(cand_track, 'genres') and cand_track.genres:
                    orig_track.genres = cand_track.genres

                # Add back to album
                self.original_album.tracks.append(orig_track)

            elif match.original_track:
                # Keep unmatched original track as-is
                self.original_album.tracks.append(match.original_track)

    def _copy_album_metadata(self):
        """Copy album-level metadata from candidate album to original album."""
        # Copy basic album information
        self.original_album.artist = self.candidate_album.artist
        self.original_album.title = self.candidate_album.title

        # Copy release information
        if hasattr(self.candidate_album, 'date') and self.candidate_album.date:
            self.original_album.date = self.candidate_album.date
        if hasattr(self.candidate_album, 'original_date') and self.candidate_album.original_date:
            self.original_album.original_date = self.candidate_album.original_date

        # Copy label and catalog information
        if hasattr(self.candidate_album, 'label') and self.candidate_album.label:
            self.original_album.label = self.candidate_album.label
        if hasattr(self.candidate_album, 'catalog_nums') and self.candidate_album.catalog_nums:
            self.original_album.catalog_nums = self.candidate_album.catalog_nums
        if hasattr(self.candidate_album, 'barcode') and self.candidate_album.barcode:
            self.original_album.barcode = self.candidate_album.barcode

        # Copy format information
        if hasattr(self.candidate_album, 'media') and self.candidate_album.media:
            self.original_album.media = self.candidate_album.media
        if hasattr(self.candidate_album, 'country') and self.candidate_album.country:
            self.original_album.country = self.candidate_album.country

        # Copy disc and track totals
        if hasattr(self.candidate_album, 'disc_total') and self.candidate_album.disc_total:
            self.original_album.disc_total = self.candidate_album.disc_total
        if hasattr(self.candidate_album, 'track_total') and self.candidate_album.track_total:
            self.original_album.track_total = self.candidate_album.track_total

        # Copy MusicBrainz ID if available
        if hasattr(self.candidate_album, 'mb_album_id'):
            self.original_album.custom['mb_album_id'] = self.candidate_album.custom.get('mb_album_id')
        if hasattr(self.candidate_album, 'mb_albumartist_id'):
            self.original_album.custom['mb_albumartist_id'] = self.candidate_album.custom.get('mb_albumartist_id')

        # Copy genres if available
        if hasattr(self.candidate_album, 'genres') and self.candidate_album.genres:
            self.original_album.genres = self.candidate_album.genres

    def _invalidate_row_cache(self):
        """Invalidate the cached row structure when matches change."""
        if hasattr(self, '_current_all_rows'):
            delattr(self, '_current_all_rows')


def create_interactive_matching_interface(original_album: Album, candidate: CandidateAlbum) -> str:
    """Create the interactive track matching interface."""
    matcher = InteractiveTrackMatcher(original_album, candidate.album)

    # Create key bindings
    kb = KeyBindings()

    @kb.add('up')
    def move_up(event):
        matcher.move_up()

    @kb.add('down')
    def move_down(event):
        matcher.move_down()

    @kb.add('left')
    @kb.add('right')
    def switch_side(event):
        matcher.switch_side()

    @kb.add('space')
    def toggle_match(event):
        matcher.toggle_match()

    @kb.add('m')
    def manual_match_key(event):
        matcher.toggle_match()  # Use same logic as space

    @kb.add('u')
    def unmatch(event):
        matcher.unmatch_current()

    @kb.add('r')
    def reset(event):
        matcher.reset_to_auto()

    @kb.add('h')
    def toggle_help(event):
        matcher.toggle_help()

    @kb.add('enter')
    def confirm(event):
        matcher.confirm_matches()

    @kb.add('q')
    def quit_interactive(event):
        matcher.cancel_matching()

    @kb.add('c-c')  # Ctrl+C - exit Python entirely
    def force_exit(event):
        print("\n🛑 Exiting Moe...")
        raise SystemExit(0)

    # Create the layout
    def get_content():
        return matcher.get_formatted_text()

    layout = Layout(
        HSplit([
            Window(
                content=FormattedTextControl(get_content),
                wrap_lines=True,
            )
        ])
    )

    # Custom style with colorful, easy-to-parse colors
    style = Style.from_dict({
        'title': '#ansiblue bold',
        'info': '#ansicyan',
        'help': '#ansiyellow',
        'header': '#ansiwhite bold',
        'summary': '#ansimagenta',
        'selection_info': '#ansicyan bold',
        'match_info': '#ansiwhite',

        # Match quality styles
        'current_good_match': '#ansigreen bold',      # Current row, good match (80%+)
        'current_ok_match': '#ansiyellow bold',       # Current row, ok match (50-80%)
        'current_poor_match': '#ansired bold',        # Current row, poor match (<50%)
        'current_unmatched': '#ansiwhite bold',       # Current row, unmatched
        'current_selected': '#ansimagenta bold',      # Currently selected track (bright highlight)
        'locked_track': '#ansiyellow bold',           # Locked track (orange/yellow)

        'good_match': '#ansigreen',                   # Good match (80%+)
        'ok_match': '#ansiyellow',                    # Ok match (50-80%)
        'poor_match': '#ansired',                     # Poor match (<50%)
        'unmatched': '#888888',                       # Unmatched (gray)
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
        result = matcher.result or "cancelled"

        # Apply matches if confirmed
        if result == "confirmed":
            matcher.apply_matches_to_albums()
            print("✅ Track matches applied successfully!")
        else:
            print("❌ Track matching cancelled - no changes made")

        return result
    except (KeyboardInterrupt, EOFError):
        print("\n❌ Track matching cancelled - no changes made")
        return "cancelled"


def interactive_track_matching(new_album: Album, candidate: CandidateAlbum):
    """Main function called by the import prompt choice."""
    result = create_interactive_matching_interface(new_album, candidate)

    # Note: If confirmed, the matches have already been applied to new_album
    # The import process will continue with the updated album


@moe.hookimpl
def add_import_prompt_choice(prompt_choices):
    """Add interactive track matching choice to the import prompt."""
    prompt_choices.append(
        PromptChoice(
            title="Edit track matches",
            shortcut_key="e",
            func=interactive_track_matching
        )
    )
