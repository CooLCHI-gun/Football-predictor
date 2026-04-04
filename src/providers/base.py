from pathlib import Path
from typing import Protocol


class CSVTemplateProvider(Protocol):
    def required_columns(self) -> dict[str, list[str]]:
        ...

    def validate_templates(self, base_dir: Path) -> list[str]:
        ...
