from dataclasses import dataclass


@dataclass
class ManifestIssue:
    field: str
    message: str
    level: str


class ManifestValidationError(Exception):
    def __init__(self, errors: list[ManifestIssue]):
        self.errors = errors
        msgs = "; ".join(f"[{e.level}] {e.field}: {e.message}" for e in errors)
        super().__init__(msgs)
