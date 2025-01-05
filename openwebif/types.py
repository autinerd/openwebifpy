"""Types for openwebif."""

from typing import TypedDict


class Bouquets(TypedDict):
    """The bouquets type."""

    bouquets: list[list[str]]
