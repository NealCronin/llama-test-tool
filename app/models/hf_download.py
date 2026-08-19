"""Data model for Hugging Face downloads driven by the official ``hf`` CLI.

The request is a frozen, secret-free snapshot: once enqueued, a job's settings
can no longer be changed by the form. Authentication never travels through this
model — the ``hf`` CLI uses its own stored credentials (``hf auth login``) or
an inherited ``HF_TOKEN`` environment variable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HfTarget(str, Enum):
    """Download destination. Configured folders refresh application selectors."""

    MODELS = "models"
    MMProj = "mmproj"
    DRAFTERS = "drafters"
    TEMPLATES = "templates"
    CUSTOM = "custom"
    CACHE = "cache"


TARGET_SETTING_KEYS = {
    HfTarget.MODELS: "models_folder",
    HfTarget.MMProj: "mmproj_folder",
    HfTarget.DRAFTERS: "drafters_folder",
    HfTarget.TEMPLATES: "template_folder",
}

TARGET_LABELS = {
    HfTarget.MODELS: "Models Folder",
    HfTarget.MMProj: "MMProj Folder",
    HfTarget.DRAFTERS: "Drafters Folder",
    HfTarget.TEMPLATES: "Chat Templates Folder",
    HfTarget.CUSTOM: "Custom Folder",
    HfTarget.CACHE: "HF Cache Only",
}


class HfRepoType(str, Enum):
    MODEL = "model"
    DATASET = "dataset"
    SPACE = "space"


class HfSelectionMode(str, Enum):
    ENTIRE = "entire"
    EXACT = "exact"
    PATTERNS = "patterns"


class HfJobState(str, Enum):
    QUEUED = "Queued"
    PREVIEWING = "Previewing"
    DOWNLOADING = "Downloading"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass(frozen=True)
class HfDownloadRequest:
    """Immutable description of one ``hf download`` run. No secrets in here."""

    repo_id: str
    target: HfTarget = HfTarget.MODELS
    repo_type: HfRepoType = HfRepoType.MODEL
    selection_mode: HfSelectionMode = HfSelectionMode.ENTIRE
    filenames: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    revision: str = ""
    local_dir: str = ""
    cache_dir: str = ""
    force_download: bool = False
    max_workers: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_id", self.repo_id.strip())
        object.__setattr__(self, "revision", self.revision.strip())
        object.__setattr__(self, "local_dir", self.local_dir.strip())
        object.__setattr__(self, "cache_dir", self.cache_dir.strip())
        object.__setattr__(self, "filenames", tuple(part.strip() for part in self.filenames if part.strip()))
        object.__setattr__(self, "include", tuple(part.strip() for part in self.include if part.strip()))
        object.__setattr__(self, "exclude", tuple(part.strip() for part in self.exclude if part.strip()))
        if not self.repo_id:
            raise ValueError("Repo ID is required.")
        if self.selection_mode is HfSelectionMode.ENTIRE and (self.filenames or self.include):
            raise ValueError("Entire-repository downloads take no file names or include patterns (exclude is allowed).")
        if self.selection_mode is HfSelectionMode.EXACT and not self.filenames:
            raise ValueError("Exact file selection needs at least one file name.")
        if self.selection_mode is HfSelectionMode.PATTERNS and not self.include:
            raise ValueError("Pattern selection needs at least one include pattern.")
        if self.local_dir and self.cache_dir:
            raise ValueError("A local folder and a custom cache folder are mutually exclusive; the CLI ignores --cache-dir when --local-dir is set.")
        if self.target is HfTarget.CACHE and self.local_dir:
            raise ValueError("HF Cache Only must not set a local folder.")
        if self.target is HfTarget.CUSTOM and not self.local_dir:
            raise ValueError("Custom Folder needs a folder path.")
        if self.max_workers is not None and self.max_workers < 1:
            raise ValueError("Max workers must be a positive integer.")

    @property
    def target_label(self) -> str:
        return TARGET_LABELS[self.target]

    @property
    def refreshes_selectors(self) -> bool:
        """True when the download lands in a configured application folder."""
        return self.target in TARGET_SETTING_KEYS

    def selection_summary(self) -> str:
        if self.selection_mode is HfSelectionMode.EXACT:
            return ", ".join(self.filenames)
        if self.selection_mode is HfSelectionMode.PATTERNS:
            summary = "include " + ", ".join(self.include)
            if self.exclude:
                summary += "; exclude " + ", ".join(self.exclude)
            return summary
        if self.exclude:
            return "entire repository; exclude " + ", ".join(self.exclude)
        return "entire repository"

    def describe(self) -> str:
        parts = [f"{self.repo_id} ({self.repo_type.value})", self.selection_summary(), self.target_label]
        if self.revision:
            parts.append(f"rev {self.revision}")
        return "  ·  ".join(parts)


@dataclass(frozen=True)
class HfCliInfo:
    """Detected CLI location and huggingface_hub version."""

    path: str
    hub_version: str = ""


@dataclass(frozen=True)
class HfCliCapabilities:
    """Optional ``hf download`` options the installed CLI actually supports.

    Determined by parsing ``hf download --help`` asynchronously at startup; a
    request needing an unsupported option is rejected before any process is
    spawned. Old CLIs therefore degrade gracefully instead of failing mid-run.
    """

    path: str
    hub_version: str = ""
    repo_type: bool = True
    revision: bool = True
    include: bool = True
    exclude: bool = True
    cache_dir: bool = True
    local_dir: bool = True
    force_download: bool = True
    dry_run: bool = True
    max_workers: bool = True


@dataclass(frozen=True)
class HfAuthStatus:
    """Outcome of ``hf auth whoami`` (official credential handling only)."""

    authenticated: bool = False
    username: str = ""

    @property
    def label(self) -> str:
        if not self.authenticated:
            return "Not authenticated (public repos only)"
        return f"Authenticated as {self.username}" if self.username else "Authenticated"


@dataclass(frozen=True)
class HfDryRunFile:
    filename: str
    size_text: str = ""
    will_download: bool = False


@dataclass(frozen=True)
class HfDryRunReport:
    """Parsed ``hf download --dry-run`` output.

    ``parsed`` is False when the output could not be interpreted; the UI must
    then show ``raw`` as-is and never fabricate structured rows.
    """

    exit_code: int = 0
    total_files: int = 0
    transfer_files: int = 0
    transfer_text: str = ""
    files: tuple[HfDryRunFile, ...] = ()
    parsed: bool = False
    raw: str = ""


@dataclass(frozen=True)
class HfDownloadResult:
    """Outcome of one download job.

    ``ok`` is True only for a normal process exit with code 0; the folder diff
    (``new_files``) is supplemental, so re-downloads and cached files are not
    mislabeled as failures.
    """

    request: HfDownloadRequest
    exit_code: int
    ok: bool
    detail: str
    new_files: tuple[str, ...] = ()
    output: str = ""
