"""Chip reference — what a chip is, from its part number or the mark printed on it."""

from benchhud.chips.composite import CompositeChipSource, Conflict, MergedChip, merge_chips
from benchhud.chips.datasheets import ChipDocument, DatasheetFile, DatasheetLibrary, MatchedBy
from benchhud.chips.dir_source import DirChipSource
from benchhud.chips.find import Candidate, find_chips
from benchhud.chips.marking import (
    MarkingMatch,
    MatchKind,
    marking_score,
    match_markings,
    normalise_mark,
)
from benchhud.chips.model import Chip, Document, Marking, Substitute, UsedIn, mpn_key
from benchhud.chips.source import ChipSource, InMemoryChipSource, NullChipSource

__all__ = [
    "Candidate",
    "Chip",
    "ChipDocument",
    "ChipSource",
    "CompositeChipSource",
    "Conflict",
    "DatasheetFile",
    "DatasheetLibrary",
    "DirChipSource",
    "Document",
    "InMemoryChipSource",
    "Marking",
    "MarkingMatch",
    "MatchKind",
    "MatchedBy",
    "MergedChip",
    "NullChipSource",
    "Substitute",
    "UsedIn",
    "find_chips",
    "marking_score",
    "match_markings",
    "merge_chips",
    "mpn_key",
    "normalise_mark",
]
