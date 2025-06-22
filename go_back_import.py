"""Plugin to add a 'Go back' option to the import confirmation prompt.

This allows users to return to the candidate selection screen without having to abort
and restart the import process.
"""

import logging
from typing import TYPE_CHECKING

import moe
from moe.util.cli import PromptChoice

if TYPE_CHECKING:
    from moe.library import Album
    from moe.moe_import.import_core import CandidateAlbum

log = logging.getLogger("moe.plugin.go_back_import")


class GoBackToMatches(Exception):
    """Exception raised to signal going back to candidate selection."""
    pass


# Store the original function to call it later
_original_select_candidate = None


@moe.hookimpl
def plugin_registration():
    """Hook into the import CLI functions after plugin registration."""
    # Import here to avoid circular imports
    from moe.moe_import import import_cli

    global _original_select_candidate

    # Store original function
    _original_select_candidate = import_cli._select_candidate

    # Replace with our wrapped version
    import_cli._select_candidate = _wrapped_select_candidate


@moe.hookimpl
def add_import_prompt_choice(prompt_choices: list[PromptChoice]):
    """Add a 'Go back' choice to the import confirmation prompt."""
    prompt_choices.append(
        PromptChoice(
            title="Go back to matches",
            shortcut_key="b",
            func=_go_back_to_matches
        )
    )


def _go_back_to_matches(new_album: "Album", candidate: "CandidateAlbum"):
    """Go back to the candidate selection screen."""
    log.debug("User chose to go back to candidate selection")
    raise GoBackToMatches("Going back to candidate selection")


def _wrapped_select_candidate(
    new_album: "Album", candidates: list["CandidateAlbum"], candidate_num: int
):
    """Wrapped version of _select_candidate that handles going back."""
    while True:
        try:
            # Call the original import_prompt through the original _select_candidate
            from moe.moe_import.import_cli import import_prompt
            import_prompt(new_album, candidates[candidate_num])
            break  # If we get here, the import was successful or aborted
        except GoBackToMatches:
            # User wants to go back to candidate selection
            log.debug("Going back to candidate selection")
            from moe.moe_import.import_cli import candidate_prompt

            # Call candidate_prompt again, which will handle the user's choice
            candidate_prompt(new_album, candidates)
            break  # candidate_prompt will handle the next selection
