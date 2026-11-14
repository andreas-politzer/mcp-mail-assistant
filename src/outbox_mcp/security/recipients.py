"""The allow-list of recipients. Free-form addresses are never accepted."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from ..errors import ConfigError, RecipientNotAllowed, ValidationError
from .validation import validate_address, validate_display_name


@dataclass(frozen=True)
class Recipient:
    name: str
    address: str
    aliases: Tuple[str, ...] = ()


class RecipientList:
    def __init__(self, recipients: Iterable[Recipient]) -> None:
        self._recipients = tuple(recipients)
        seen_addresses = set()
        seen_names = set()
        for r in self._recipients:
            address_key = r.address.casefold()
            if address_key in seen_addresses:
                raise ConfigError(f"Recipient address listed twice: {r.address}")
            seen_addresses.add(address_key)
            for label in (r.name, *r.aliases):
                key = label.casefold()
                if key in seen_names:
                    raise ConfigError(f"Recipient name or alias used twice: {label}")
                seen_names.add(key)

    @classmethod
    def from_file(cls, path: Path) -> "RecipientList":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError(f"Recipients file not found: {path}") from None
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Recipients file could not be read: {exc.__class__.__name__}") from None
        return cls.from_data(raw)

    @classmethod
    def from_data(cls, raw: object) -> "RecipientList":
        if not isinstance(raw, dict) or not isinstance(raw.get("recipients"), list):
            raise ConfigError('Recipients file must look like {"recipients": [...]}.')
        recipients = []
        for item in raw["recipients"]:
            if not isinstance(item, dict):
                raise ConfigError("Every recipient must be an object with name and address.")
            try:
                name = validate_display_name(item.get("name", ""))
                address = validate_address(item.get("address", ""))
                aliases = tuple(validate_display_name(a) for a in item.get("aliases", []))
            except ValidationError as exc:
                raise ConfigError(f"Invalid recipient entry: {exc}") from None
            if not name:
                raise ConfigError("Every recipient needs a name.")
            recipients.append(Recipient(name=name, address=address, aliases=aliases))
        return cls(recipients)

    def all(self) -> Tuple[Recipient, ...]:
        return self._recipients

    def resolve(self, key: object) -> Recipient:
        """Find a recipient by name, alias or exact address. Anything else is refused."""
        text = key.strip().casefold() if isinstance(key, str) else ""
        matches = [
            r
            for r in self._recipients
            if text
            and (
                text == r.name.casefold()
                or text == r.address.casefold()
                or text in (a.casefold() for a in r.aliases)
            )
        ]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(r.name for r in self._recipients) or "(none)"
        if len(matches) > 1:
            raise RecipientNotAllowed(f"The recipient is ambiguous. Allowed recipients: {names}.")
        raise RecipientNotAllowed(
            f"This recipient is not on the allowed recipient list. Allowed recipients: {names}."
        )

    def is_allowed_address(self, address: str) -> bool:
        return any(r.address.casefold() == address.casefold() for r in self._recipients)
