from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DetectedTool:
    name: str
    paths: tuple[Path, ...]

    @property
    def found(self) -> bool:
        return bool(self.paths)


@dataclass(frozen=True)
class LlamaCppInstallation:
    folder: Path
    server: DetectedTool
    fit_params: DetectedTool

    def tool(self, name: str) -> DetectedTool:
        return {"llama-server": self.server, "llama-fit-params": self.fit_params}[name]


class LlamaCppInstallationService:
    """Find llama.cpp executables only underneath the explicitly selected root."""

    TOOL_NAMES = ("llama-server", "llama-fit-params")
    BUILD_DIRS = (
        Path("bin"), Path("bin/Release"), Path("bin/Debug"),
        Path("build/bin"), Path("build/bin/Release"), Path("build/bin/Debug"),
    )

    @classmethod
    def discover(cls, folder: str | Path) -> LlamaCppInstallation:
        root = Path(folder)
        if not folder or not root.is_dir():
            return LlamaCppInstallation(root, *(DetectedTool(name, ()) for name in cls.TOOL_NAMES))
        candidates = {name: set() for name in cls.TOOL_NAMES}
        for directory in cls._candidate_dirs(root):
            cls._collect(directory, candidates)
        # Builds commonly use nonstandard names such as build-vulkan/build-mixed.
        # Search only this chosen tree, cap the result, and avoid the VCS tree.
        if any(not paths for paths in candidates.values()):
            count = 0
            for path in root.rglob("*"):
                if count >= 2_000:
                    break
                if ".git" in path.parts[len(root.parts):]:
                    continue
                if path.is_file():
                    count += 1
                    cls._collect_path(path, candidates)
        return LlamaCppInstallation(
            root,
            DetectedTool("llama-server", tuple(sorted(candidates["llama-server"], key=cls._sort_key))),
            DetectedTool("llama-fit-params", tuple(sorted(candidates["llama-fit-params"], key=cls._sort_key))),
        )

    @classmethod
    def active_server(cls, settings) -> str:
        manual = Path(settings.llama_server_executable) if settings.llama_server_executable else None
        if manual and manual.is_file():
            return str(manual)
        return cls._active_discovered(settings.llama_server_selected, settings.llama_cpp_folder, "llama-server")

    @classmethod
    def active_fit_params(cls, settings) -> str:
        return cls._active_discovered(settings.llama_fit_params_executable, settings.llama_cpp_folder, "llama-fit-params")

    @classmethod
    def _active_discovered(cls, selected: str, folder: str, tool: str) -> str:
        path = Path(selected) if selected else None
        if path and path.is_file():
            return str(path)
        discovered = cls.discover(folder).tool(tool).paths
        return str(discovered[0]) if discovered else ""

    @classmethod
    def _candidate_dirs(cls, root: Path) -> set[Path]:
        directories = {root / item for item in cls.BUILD_DIRS}
        directories.update(directory / suffix for directory in root.glob("build*") if directory.is_dir() for suffix in (Path("bin"), Path("bin/Release"), Path("bin/Debug")))
        return {directory for directory in directories if directory.is_dir()}

    @classmethod
    def _collect(cls, directory: Path, candidates: dict[str, set[Path]]) -> None:
        for path in directory.iterdir():
            if path.is_file():
                cls._collect_path(path, candidates)

    @classmethod
    def _collect_path(cls, path: Path, candidates: dict[str, set[Path]]) -> None:
        name = path.name.casefold()
        for tool in cls.TOOL_NAMES:
            if name in {tool, f"{tool}.exe"}:
                candidates[tool].add(path.resolve())

    @staticmethod
    def _sort_key(path: Path) -> tuple[int, str]:
        text = str(path).casefold()
        # Prefer Release candidates, then shorter paths; selections still remain editable.
        return (0 if "release" in text else 1, len(text), text)
