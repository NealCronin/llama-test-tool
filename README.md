# Llama Test Tool

A local PySide6 desktop application for building, testing, persisting, and managing `llama-server` commands. It keeps commands as structured argv data, then renders a correctly quoted presentation string only for copying or llama-swap YAML.

## Installation and launch

Requires Python 3.11 or newer.

```powershell
cd llama-test-tool
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The application stores its own settings under the platform-specific configuration directory returned by `platformdirs` for **Llama Test Tool**; it does not create settings next to `llama-server` or beside a GGUF.

## First use

Open **Settings** and choose:

- **llama.cpp Folder** first. The tool discovers `llama-server` and `llama-fit-params` beneath standard `build/bin` locations and known build directories such as `build-mixed`. Use **Rescan** and choose active binaries if several builds are present.
- Models, MMProj, MTP/draft, DFlash, optional DSpark/generic draft, and chat-template folders.
- An existing llama-swap YAML configuration file.

The manual `llama-server` executable remains available as an advanced override; a valid override wins over the folder-discovered server.

Every path remains editable. A missing prior path is colored as unavailable rather than silently cleared. Save settings, then return to **Command Builder**; folder-backed selectors refresh from these locations and have individual refresh buttons.

Models are scanned recursively and sorted naturally. The scanner displays `.gguf` files and reduces clear `part-01`/`split-01` multipart GGUF sets to their first loader shard. Template selectors list `.jinja`, `.jinja2`, `.txt`, and `.tmpl`; manual Browse always allows any file.

## Building and testing commands

A new command always begins with the non-removable `-m` model selector. Use **Add Argument** to search aliases, names, and upstream descriptions (`context`, `gpu`, `flash`, `draft`, `template`, and similar terms work). Rows choose appropriate editors for parameterless flags, enums, multi-value flags, models, MMProj files, external draft models, template files, and comma-separated `--spec-type` values. Use arrows to control argument order.

The preview is read-only. **Copy Command** copies a Windows-safe quoted command. **Vertical preview** emits a readable `^`-continued display. The command state—argument order, values, source selections, and model—is saved automatically, not as one mutable shell string.

**Test Command** validates the structured command and starts the configured executable through `QProcess`, never through a shell. Output streams to the process console and **Stop** requests clean termination before killing if required. `${PORT}` is substituted with an available local port only for this local execution; the saved command remains unchanged.

## Memory Test

**Memory Test** runs the active `llama-fit-params` asynchronously against the structured command currently in the builder. It first asks the installed utility which aliases it accepts, then forwards only builder arguments supported by that exact binary. Server-only flags such as `--host` and `--port` are therefore not sent to the fitter.

The first pass uses llama.cpp's `--fit-print on` output for the current requested configuration. The second pass obtains fitted CLI arguments. The result window reports **Model Weights**, **Context / KV Cache**, **Compute Buffers**, per-device/Host rows, GiB primary values with MiB tooltips, fitted arguments, and complete combined stdout/stderr raw output. It never changes the builder unless **Apply Fitted Parameters** is pressed.

Use **Memory Test Options** only to override llama.cpp's normal fit defaults with a free-memory target (`--fit-target`) or minimum fitted context (`--fit-ctx`). Leave both blank to retain the installed llama.cpp defaults. **Cancel Memory Test** terminates an unusually long analysis without freezing the UI.

## Templates and speculative decoding

- **Built-in chat templates:** `--chat-template` presents documented llama.cpp template names plus a custom-value path. It does not require a file.
- **Custom template file:** `--chat-template-file` uses the template folder. Adding either custom-template option automatically puts `--jinja` before it when unambiguous.
- **Built-in MTP:** choose **Presets → Built-in MTP**. It adds `--spec-type draft-mtp` only. Built-in MTP uses heads in the main model and never requires `-md` or the MTP folder.
- **External draft:** `-md` has a source selector for MTP/draft, DFlash, DSpark, generic draft, or a manually browsed file.
- **DFlash / DSpark:** their presets add normal `--spec-type draft-dflash` / `draft-dspark` rows. Select an external `-md` yourself; the tool does not silently inject tuning values.
- **N-gram Mod:** adds normal `--spec-type ngram-mod`, which remains editable.

All these presets are ordinary rows and can be changed or removed after insertion.

## llama-swap

Choose the llama-swap YAML in Settings, then use **Add to llama-swap**. Enter a model ID (a sanitized GGUF filename is suggested) and optional display name. The generated command is a readable multiline `cmd: |` value. If there is no existing port option the tool adds `--port ${PORT}` for llama-swap; a deliberately entered fixed/custom port is preserved. Testing does not replace that macro in saved state.

The **llama-swap Config** tab searches `models:` entries and shows model ID, display name, command, detected model path, and missing-file status. It can load visual-builder-compatible commands, save only a selected entry's `cmd`, duplicate an entry, or remove a selected entry after confirmation. Imported commands with unsupported flags, malformed quoting, or shell syntax stay in clearly labeled **Raw Command Mode** instead of being rewritten unsafely.

YAML edits use `ruamel.yaml` round-trip mode. Each write parses the existing YAML, serializes to a sibling temporary file, reparses it, creates `config.yaml.bak-YYYYMMDD-HHMMSS`, then atomically replaces the original. Updating a command preserves other fields on that model (`ttl`, `aliases`, `env`, and so on), other models, comments as far as round-trip YAML supports them, and unrelated top-level settings. The default backup retention is 10 newest backups.

## Argument catalog

`data/llama_server_flags.json` is a generated offline catalog from llama.cpp's auto-generated `tools/server/README.md`; the UI also has **Catalog → Refresh llama.cpp Arguments**. Refresh downloads and reparses the current upstream README, replacing the bundled copy only after a successful parse. Failure leaves the active bundled catalog untouched.

The documentation is fundamentally prose/Markdown rather than a machine schema. The parser reliably extracts aliases, common value placeholders, braced/bracketed enums, the bare comma-separated `--spec-type` list, and built-in template names. Some complex optional syntaxes such as nested Hugging Face repository grammar remain plain editable text fields by design.

## Tests

```powershell
cd llama-test-tool
python -m pytest -q tests
```

Tests cover catalog arities/aliases/enums, rendered argv quoting and `${PORT}`, command import, builder persistence and missing-file restoration, round-trip YAML add/update/remove preservation, backups, and malformed-YAML refusal.
