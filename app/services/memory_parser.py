from __future__ import annotations

import re
import shlex

from app.models.memory import DeviceMemoryBreakdown, MemoryBreakdown


class MemoryBreakdownParser:
    """Parser for llama.cpp's whitespace/prefix-tolerant MiB breakdown table."""

    _HEADER = re.compile(r"memory\s+breakdown\s*\[?\s*mib\s*]?", re.I)
    _ROW = re.compile(r"\|\s*(?:-\s*)?(?P<device>[^|]+?)\s*\|\s*(?P<values>[^|]+?)\s*\|")
    _NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")

    @classmethod
    def parse(cls, output: str) -> MemoryBreakdown | None:
        lines = output.splitlines()
        header_index = next((index for index, line in enumerate(lines) if cls._HEADER.search(line) and all(column in line.casefold() for column in ("model", "context", "compute"))), None)
        if header_index is not None:
            devices: list[DeviceMemoryBreakdown] = []
            for line in lines[header_index + 1:]:
                if cls._HEADER.search(line):
                    continue
                match = cls._ROW.search(line)
                if not match:
                    if devices and "|" not in line:
                        break
                    continue
                values = cls._parse_values(match.group("values"))
                if values is not None:
                    devices.append(DeviceMemoryBreakdown(device_name=match.group("device").strip(), **values))
            if devices:
                return MemoryBreakdown(tuple(devices))
        return cls._parse_compact(lines)
    @classmethod
    def _parse_compact(cls, lines: list[str]) -> MemoryBreakdown | None:
        devices: list[DeviceMemoryBreakdown] = []
        pattern = re.compile(r"^(?P<device>[^:|]+?)\s+(?P<model>\d+(?:\.\d+)?)\s+(?P<context>\d+(?:\.\d+)?)\s+(?P<compute>\d+(?:\.\d+)?)\s*$")
        for line in lines:
            match = pattern.match(line.strip())
            if not match:
                continue
            device = match.group("device").strip()
            # llama.cpp logs use prefixes containing ':'; accept only compact device rows.
            if not device or ":" in device:
                continue
            model, context, compute = (float(match.group(name)) for name in ("model", "context", "compute"))
            devices.append(DeviceMemoryBreakdown(device, model, context, compute, self_mib=model + context + compute))
        return MemoryBreakdown(tuple(devices)) if devices else None


    @classmethod
    def _parse_values(cls, cell: str) -> dict[str, float | None] | None:
        numbers = [float(value) for value in cls._NUMBER.findall(cell)]
        # GPU/device form: total = free + (self = model + context + compute) + unaccounted
        if "(" in cell and len(numbers) >= 6:
            total, free, self_mib, model, context, compute = numbers[:6]
            return {
                "total_mib": total, "free_mib": free, "self_mib": self_mib,
                "model_mib": model, "context_mib": context, "compute_mib": compute,
                "unaccounted_mib": numbers[6] if len(numbers) > 6 else None,
            }
        # Host/other buffer form: self = model + context + compute
        if len(numbers) >= 4:
            self_mib, model, context, compute = numbers[:4]
            return {
                "total_mib": None, "free_mib": None, "self_mib": self_mib,
                "model_mib": model, "context_mib": context, "compute_mib": compute,
                "unaccounted_mib": None,
            }
        return None


def parse_fitted_arguments(output: str) -> tuple[str, ...]:
    """Return only a conservative, shell-free fitted argument line from llama-fit-params."""
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("-") or any(symbol in candidate for symbol in ("|", "&", ";", "`")):
            continue
        try:
            tokens = tuple(shlex.split(candidate, posix=False))
        except ValueError:
            continue
        if tokens and all(token and (token.startswith("-") or not token.startswith(("|", "&", ";"))) for token in tokens):
            return tuple(token[1:-1] if len(token) > 1 and token[:1] == token[-1:] == '"' else token for token in tokens)
    return ()


def parse_supported_flags(help_output: str) -> frozenset[str]:
    """Extract aliases from the installed utility's --help instead of carrying a static whitelist."""
    return frozenset(re.findall(r"(?<!\w)(--?[A-Za-z][\w-]*)", help_output))
