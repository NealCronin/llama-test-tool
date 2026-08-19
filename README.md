# Llama Test Tool

A local PySide6 desktop application for the full local GGUF lifecycle: **download** model files, **configure** `llama-server`, **estimate memory**, **benchmark** inference, **test-launch** the server, and **persist the tested configuration** in llama-swap — all in one place, without ever hiding the underlying commands.

Commands are kept as structured argv data (`executable` + ordered arguments), not one mutable shell string. That same structured configuration is reused for preview/copy, test launches, `llama-fit-params`, `llama-bench`, and llama-swap generation. Each downstream utility accepts a different subset and syntax, so the tool translates semantically, records skipped arguments and approximations, and shows the raw utility output. A test or benchmark run never mutates the builder.

## Installation and launch

Requires Python 3.11 or newer.

```powershell
cd llama-test-tool
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

For the Hugging Face tab you also need the official `hf` CLI on `PATH`:

```powershell
python -m pip install -U huggingface_hub
```

The application stores its own settings under the platform-specific configuration directory returned by `platformdirs` for **Llama Test Tool**; it does not create settings next to `llama-server` or beside a GGUF.

## First use

Open **Settings** and choose:

- **llama.cpp Folder** first. The tool discovers `llama-fit-params` and `llama-bench` beneath standard `build/bin` locations and known build directories such as `build-mixed`. Use **Rescan** and choose the active binaries if several builds are present.
- **Models**, **MMProj**, **Drafters**, and **Chat Template** folders. There is one general Drafters folder; MTP, DFlash, DSpark, and any other external speculative/draft models all live there.
- An existing llama-swap YAML configuration file (needed only for the llama-swap features).

The `llama-server` used by the application is fixed at `Engines/llama.cpp/build-mixed/bin/Release/llama-server.exe`, shown read-only under **Detected Tools**. Preview, copy, imported commands, test launches, and llama-swap command generation all correspond to that server; for local execution its relative spelling is resolved to an absolute path. There is deliberately no per-command or per-model engine selection.

Every path remains editable. A missing prior path is colored as unavailable rather than silently cleared. Save settings, then return to **Command Builder**; folder-backed selectors refresh from these locations and have individual refresh buttons.

Models are scanned recursively and sorted naturally. The scanner displays `.gguf` files and reduces clear `part-01`/`split-01` multipart GGUF sets to their first loader shard. Template selectors list `.jinja`, `.jinja2`, `.txt`, and `.tmpl`; manual Browse always allows any file.

## Hugging Face downloads

The **Hugging Face** tab is a GUI and process manager around the installed official `hf` CLI — it does not implement a custom downloader. Every CLI interaction (version, `auth whoami`, `download --help` capability probing, dry-run previews, downloads) runs asynchronously through `QProcess`; only `PATH` discovery is synchronous, so a slow or misconfigured `hf` can never block the GUI.

The status row shows the `hf` CLI path, the `huggingface_hub` version, and either **Authenticated as \<user\>** or **Not authenticated**. **Refresh Status** re-probes; **Open Login Terminal** and **Copy Login Command** run or copy `hf auth login` interactively. There is deliberately no token field: authentication uses the `hf` CLI's own stored credentials, an inherited `HF_TOKEN` environment value works as-is, and secrets are redacted from every preview, console line, queue cell, and error detail.

1. Enter a **Repo ID** (`owner/repo`) and choose the **Repository Type** (Model, Dataset, or Space — Model is the default and omits the flag; dataset/space add `--repo-type`).
2. Add an optional revision (branch, tag, or commit) and choose the file selection: **Entire Repository**, **Exact File Names** (comma-separated), or **Include / Exclude Patterns** (glob lists).
3. Choose the **Destination** — Models, MMProj, Drafters, or Chat Templates folders from Settings, **HF Cache Only** (with optional custom cache directory), or a **Custom Folder**.
4. Options: **Force Download** (re-download even if cached) and an optional **max workers** override.
5. The **Command Preview** shows the exact `hf download …` argv, updated live. **Preview Download (dry-run)** runs the real `hf download … --dry-run` (huggingface_hub 1.0.0+) and reports the file list and transfer size without touching the destination.
6. **Add to Queue / Start** runs the items one at a time with a live console, a queue table (Repository / Type / Selection / Destination / State / Result), **Cancel Active** to terminate the running download, and **Remove Queued** to drop pending items.

At startup the tab parses `hf download --help` and records which optional flags the installed release actually supports (`--repo-type`, `--revision`, `--include`, `--exclude`, `--cache-dir`, `--local-dir`, `--force-download`, `--dry-run`, `--max-workers`). If a feature is missing, the corresponding control is disabled with a compatibility note instead of passing an unsupported argument. After a run, the target folder is diffed to report precisely which files were newly created; a glob that matches nothing is flagged in the queue result rather than reported as a clean download. After a successful download the matching builder selectors refresh automatically.

## Building and testing commands

A new command always begins with the non-removable `-m` model selector. Use **Add Argument** to search aliases, names, and upstream descriptions (`context`, `gpu`, `flash`, `draft`, `template`, and similar terms work). Rows choose appropriate editors for parameterless flags, enums, multi-value flags, models, MMProj files, external draft models, template files, and comma-separated `--spec-type` values. Use arrows to control argument order.

Common flags are surfaced in a curated list; the complete llama.cpp argument catalog remains available under Advanced.

The preview is read-only. **Copy Command** copies a Windows-safe quoted command. **Vertical preview** emits a readable `^`-continued display. The command state—argument order, values, source selections, and model—is saved automatically.

**Test Command** validates the structured command and starts the configured executable through `QProcess`, never through a shell. Output streams to the process console and **Stop** requests clean termination before killing if required. `${PORT}` is substituted with an available local port only for this local execution; the saved command remains unchanged.

## Memory Test

**Memory Test** runs the active `llama-fit-params` asynchronously against the structured command currently in the builder. It first asks the installed utility which aliases it accepts, then forwards only builder arguments supported by that exact binary. Server-only flags such as `--host` and `--port` are therefore not sent to the fitter.

The first pass uses llama.cpp's `--fit-print on` output for the current requested configuration. The second pass obtains fitted CLI arguments. The result window reports **Model Weights**, **Context / KV Cache**, **Compute Buffers**, per-device/Host rows, GiB primary values with MiB tooltips, fitted arguments, and complete combined stdout/stderr raw output. A successful estimate remains usable even if automatic fitting fails. It never changes the builder unless **Apply Fitted Parameters** is pressed.

Use **Memory Test Options** only to override llama.cpp's normal fit defaults with a free-memory target (`--fit-target`) or minimum fitted context (`--fit-ctx`). Leave both blank to retain the installed llama.cpp defaults. **Cancel Memory Test** terminates an unusually long analysis without freezing the UI.

## Benchmark

**Benchmark** answers "how fast is this exact configuration?" with the installed `llama-bench` binary. The structured server configuration is translated semantically into llama-bench arguments, including device separators, tensor-split separators, GPU-layer representation, and the polarity differences for KV offload, operation offload, and mmap. The run options dialog sets prompt/generation token counts, batch sizes, repetitions, and a warm-up delay.

The results window shows prompt-processing and generation tokens/sec with standard deviation, backend and device information, the exact translated llama-bench arguments, any skipped server-only arguments, warnings about approximations, and the raw `llama-bench` output. These numbers are llama-bench's standalone measurement of the translation/compute pipeline — they are not llama-server or HTTP throughput.

## Templates and speculative decoding

- **Built-in chat templates:** `--chat-template` presents documented llama.cpp template names plus a custom-value path. It does not require a file.
- **Custom template file:** `--chat-template-file` uses the template folder. Adding either custom-template option automatically puts `--jinja` before it when unambiguous.
- **Built-in MTP:** choose **Presets → Built-in MTP**. It adds `--spec-type draft-mtp` only. Built-in MTP uses heads in the main model and never requires `-md` or the Drafters folder.
- **External draft:** `-md` has a source selector for the Drafters folder or a manually browsed file.
- **DFlash / DSpark:** their presets add normal `--spec-type draft-dflash` / `draft-dspark` rows. Select an external `-md` yourself; the tool does not silently inject tuning values.
- **N-gram Mod:** adds normal `--spec-type ngram-mod`, which remains editable.
- **Guided presets:** **Context + KV Cache**, **Device Split**, and **Custom MTP / External Drafter** prefill their dialogs from the current command (resolved through the catalog, so alias spellings don't matter) and only touch the arguments the preset owns.

All these presets are ordinary rows and can be changed or removed after insertion.

## llama-swap

Choose the llama-swap YAML in Settings, then use **Add to llama-swap**. Enter a model ID (a sanitized GGUF filename is suggested), a display name, and capability/metadata. The generated command is a readable multiline `cmd: |` value. If there is no existing port option the tool adds `--port ${PORT}` for llama-swap; a deliberately entered fixed/custom port is preserved. Testing does not replace that macro in saved state.

The **llama-swap Config** tab is organized as:

- **Models** — search `models:` entries by ID or name; shows model ID, display name, command, detected model path, and missing-file status. Load a visual-builder-compatible command into the builder, save only the selected entry's `cmd`, edit metadata, duplicate, or remove an entry after confirmation. Imported commands with unsupported flags, malformed quoting, or shell syntax stay in clearly labeled **Raw Command Mode** instead of being rewritten unsafely.
- **General, Logging, Activity / Performance, Security, Macros, Hooks, Upstream, Profiles, Selectors, Routing, Peers** — targeted editors for the rest of the configuration.

Every setting is **presence-aware**: an absent key, an explicitly configured key, and an effective default are shown and handled differently. Displaying a default never writes it; **Reset to Default** removes the key so llama-swap applies its own default, rather than writing the current default value. Unknown fields at any level are surfaced, and a failed validation leaves the file untouched.

YAML edits use `ruamel.yaml` round-trip mode. Each write parses the existing YAML, changes only the targeted subtree, serializes to a sibling temporary file, reparses it, validates against the bundled llama-swap JSON Schema, creates `config.yaml.bak-YYYYMMDD-HHMMSS`, then atomically replaces the original. Updating one model preserves other fields on that model (`ttl`, `aliases`, `env`, and so on), other models, comments as far as round-trip YAML supports them, and unrelated top-level settings. The default backup retention is 10 newest backups.

## Argument catalog

`data/llama_server_flags.json` is a generated offline catalog from llama.cpp's auto-generated `tools/server/README.md`; the UI also has **Catalog → Refresh llama.cpp Arguments**. Refresh downloads and reparses the current upstream README, replacing the bundled copy only after a successful parse. Failure leaves the active bundled catalog untouched.

The documentation is fundamentally prose/Markdown rather than a machine schema. The parser reliably extracts aliases, common value placeholders, braced/bracketed enums, the bare comma-separated `--spec-type` list, and built-in template names. Some complex optional syntaxes such as nested Hugging Face repository grammar remain plain editable text fields by design.

## Tests

```powershell
cd llama-test-tool
python -m pytest -q tests
```

Tests cover catalog arities/aliases/enums, rendered argv quoting and `${PORT}`, command import, builder persistence and missing-file restoration, preset dialog prefill and emission (including the Custom MTP draft-KV selection), `hf` CLI discovery/argv construction/queue behavior against a fake `hf`, round-trip YAML add/update/remove preservation, targeted llama-swap subtree writes (presence-aware globals, routing/model validation), backups, and malformed-YAML refusal.
