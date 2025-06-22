"""Plugin to add a 'Use metadata from files' option to the candidate selection prompt.

This allows users to skip online database matching and proceed with the existing
metadata from the audio files themselves.
"""

import logging
from typing import TYPE_CHECKING

import moe
from moe.util.cli import PromptChoice

if TYPE_CHECKING:
    from moe.library import Album
    from moe.moe_import.import_core import CandidateAlbum

log = logging.getLogger("moe.plugin.use_file_metadata")


@moe.hookimpl
def add_candidate_prompt_choice(prompt_choices: list[PromptChoice]):
    """Add a 'Use metadata from files' choice to the candidate selection prompt."""
    prompt_choices.append(
        PromptChoice(
            title="Use metadata from files (skip online matching)",
            shortcut_key="f",
            func=_use_file_metadata
        )
    )


def _use_file_metadata(new_album: "Album", candidates: list["CandidateAlbum"]):
    """Use the existing metadata from files instead of online matches."""
    log.debug("User chose to use metadata from files, skipping online matching")

    # Print confirmation message
    print(f"✅ Using existing metadata from files for: {new_album.artist} - {new_album.title}")

    # No need to apply any candidate metadata - just proceed with the existing album data
    # The album already has metadata read from the files, so we don't need to do anything special
    # The import process will continue with the original file metadata
    pass
