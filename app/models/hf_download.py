from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class HfTarget(str, Enum):
    MODELS = "models"
    MMProj = "mmproj"
    DRAFTERS = "drafters"
    TEMPLATES = "templates"


TARGET_SETTING_KEYS = {
    HfTarget.MODELS: "models_folder",
    HfTarget.MMProj: "mmproj_folder",
    HfTarget.DRAFTERS: "drafters_folder",
    HfTarget.TEMPLATES: "template_folder",
}

TARGET_LABELS = {
    HfTarget.MODELS: "Models",
    HfTarget.MMProj: "MMProj",
    HfTarget.DRAFTERS: "Drafters",
    HfTarget.TEMPLATES: "Chat templates",
}


@dataclass
class HfDownloadRequest:
    """One `hf download` invocation.

    Either explicit ``filenames`` or ``include`` glob patterns select the files;
    ``exclude`` narrows a glob selection. Both empty lists is rejected before
    a process is ever started.
    """

    repo_id: str
    target: HfTarget
    filenames: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    max_workers: int = 0  # 0 lets the hf CLI use its own default
    revision: str = ""
    token: str = ""

    def __post_init__(self) -> None:
        self.repo_id = self.repo_id.strip()
        self.revision = self.revision.strip()
        self.token = self.token.strip()
        self.filenames = [part.strip() for part in self.filenames if part.strip()]
        self.include = [part.strip() for part in self.include if part.strip()]
        self.exclude = [part.strip() for part in self.exclude if part.strip()]

    def describe(self) -> str:
        selection = ", ".join(self.filenames) if self.filenames else " ".join(f"--include {part}" for part in self.include)
        return f"{self.repo_id} → {TARGET_LABELS[self.target]} ({selection})"


@dataclass
class HfDownloadResult:
    request: HfDownloadRequest
    success: bool
    files: list[str] = field(default_factory=list)  # relative paths newly created in the target folder
    detail: str = ""
