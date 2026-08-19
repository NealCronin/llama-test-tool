from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir


@dataclass
class AppSettings:
    models_folder: str = ""
    mmproj_folder: str = ""
    drafters_folder: str = ""
    template_folder: str = ""
    llama_cpp_folder: str = ""
    llama_fit_params_executable: str = ""
    llama_bench_executable: str = ""
    llama_swap_config: str = ""
    memory_fit_target: str = ""
    memory_fit_context: str = ""
    picker_show_advanced: bool = False
    benchmark_prompt_tokens: int = 512
    benchmark_generation_tokens: int = 128
    benchmark_repetitions: int = 5
    benchmark_context_depth: int = 0
    benchmark_delay: int = 0
    benchmark_no_warmup: bool = False
    window_geometry: str = ""
    vertical_preview: bool = False
    backup_limit: int = 10
    last_command: dict[str, Any] = field(default_factory=dict)
    hf_destination: str = "models"
    hf_repo_type: str = "model"
    hf_custom_local_dir: str = ""
    hf_custom_cache_dir: str = ""
    hf_force_download: bool = False
    hf_worker_override: bool = False
    hf_max_workers: int = 8

    @classmethod
    def path(cls) -> Path:
        directory = Path(user_config_dir("llama-test-tool", "Llama Test Tool"))
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "settings.json"

    @classmethod
    def load(cls) -> "AppSettings":
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
            if not data.get("drafters_folder"):
                data["drafters_folder"] = next((str(data[key]) for key in ("mtp_folder", "dflash_folder", "dspark_folder", "draft_folder") if data.get(key)), "")
            valid = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
            return cls(**valid)
        except (OSError, json.JSONDecodeError, TypeError):
            return cls()
    def save(self) -> None:
        path = self.path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def paths(self) -> dict[str, str]:
        return {
            "Models folder": self.models_folder,
            "MMProj folder": self.mmproj_folder,
            "Drafters folder": self.drafters_folder,
            "Chat template folder": self.template_folder,
            "llama.cpp Folder": self.llama_cpp_folder,
            "Configured llama-server": "Engines/llama.cpp/build-mixed/bin/Release/llama-server.exe",
            "Detected llama-fit-params": self.llama_fit_params_executable,
            "Detected llama-bench": self.llama_bench_executable,
            "llama-swap config file": self.llama_swap_config,
        }
