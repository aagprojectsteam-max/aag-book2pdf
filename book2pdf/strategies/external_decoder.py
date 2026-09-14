"""Explicit authorized adapter interface. No key discovery or executable probing."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DecoderRequest:
    source: Path
    private_destination: Path
    authorization_reference: str
    output_limit: int = 32 * 1024 * 1024


class AuthorizedDecoder(Protocol):
    """Future adapters require explicit configuration and independent validation.

    Implementations must honor output_limit, never mutate source, and execute in
    the bounded worker. No adapter is registered/enabled by default. Possession
    of an opaque file does not establish encryption or a key requirement.
    """
    name: str

    def decode(self, request: DecoderRequest) -> Path: ...


REGISTERED_DECODERS: dict[str, AuthorizedDecoder] = {}
