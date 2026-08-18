from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.models.command import Command
from app.services.flag_catalog import FlagCatalog


@dataclass(frozen=True)
class ValidationIssue:
    severity: str  # error or warning
    message: str


def validate_command(command: Command, catalog: FlagCatalog) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    model = command.model_path()
    if model is None:
        issues.append(ValidationIssue("error", "Select a main model before running or saving."))
    elif not model.is_file():
        issues.append(ValidationIssue("error", f"Main model is unavailable: {model}"))

    seen: dict[str, str] = {}
    spec_types: set[str] = set()
    external_draft = False
    custom_template = False
    jinja_index: int | None = None
    template_index: int | None = None
    for position, argument in enumerate(command.arguments):
        spec = catalog.find(argument.flag)
        if spec is None:
            issues.append(ValidationIssue("warning", f"{argument.flag} is no longer in the loaded argument catalog."))
            continue
        if not spec.optional_parameter and (len(argument.values) < spec.parameter_count or any(not value.strip() for value in argument.values[:spec.parameter_count])):
            issues.append(ValidationIssue("error", f"{argument.flag} requires {spec.parameter_count} non-empty value(s)."))
        if spec.choices and argument.values and spec.value_type != "integer_or_choices":
            values = argument.values[0].split(",") if spec.special_editor == "spec_type" else argument.values
            invalid = [value for value in values if value and value not in spec.choices]
            if invalid:
                issues.append(ValidationIssue("error", f"{argument.flag} has unsupported value(s): {', '.join(invalid)}."))
        if spec.value_type in {"integer", "integer_or_choices"}:
            for value in argument.values:
                if not value or value == "${PORT}" or (spec.value_type == "integer_or_choices" and value in spec.choices):
                    continue
                try:
                    int(value)
                except ValueError:
                    expected = "an integer or one of " + ", ".join(spec.choices) if spec.value_type == "integer_or_choices" else "an integer"
                    issues.append(ValidationIssue("error", f"{argument.flag} requires {expected}, not {value!r}."))
        if spec.canonical_name in seen and not spec.repeatable:
            issues.append(ValidationIssue("warning", f"{argument.flag} duplicates {seen[spec.canonical_name]}."))
        seen[spec.canonical_name] = argument.flag
        if spec.canonical_name in {"--mmproj", "--spec-draft-model", "--chat-template-file"} and argument.values:
            path = Path(argument.values[0])
            if argument.values[0] and not path.is_file():
                issues.append(ValidationIssue("error", f"Referenced file is unavailable: {path}"))
        if spec.canonical_name == "--spec-type" and argument.values:
            spec_types.update(value for value in argument.values[0].split(",") if value)
        external_draft |= spec.canonical_name in {"--spec-draft-model", "--model-draft"} and bool(argument.values and argument.values[0])
        custom_template |= spec.canonical_name in {"--chat-template", "--chat-template-file"} and bool(argument.values and argument.values[0])
        if spec.canonical_name == "--jinja" and argument.flag != "--no-jinja":
            jinja_index = position
        if spec.canonical_name in {"--chat-template", "--chat-template-file"}:
            template_index = position
    if {"draft-dflash", "draft-dspark"} & spec_types and not external_draft:
        issues.append(ValidationIssue("error", "DFlash/DSpark speculative decoding requires an external draft model (-md)."))
    if custom_template and jinja_index is None:
        issues.append(ValidationIssue("warning", "Custom templates normally require --jinja before the template argument."))
    elif custom_template and template_index is not None and jinja_index is not None and jinja_index > template_index:
        issues.append(ValidationIssue("warning", "Move --jinja before the custom template argument."))
    return issues
