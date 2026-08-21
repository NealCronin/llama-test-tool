# Llama Test Tool

A local PySide6 desktop application for the full local GGUF lifecycle: **configure** `llama-server`, **estimate memory**, **benchmark** inference, **test-launch** the server, and **persist the tested configuration** in llama-swap — all in one place, without ever hiding the underlying commands.

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

The application stores its own settings under the platform-specific configuration directory returned by `platformdirs` for **Llama Test Tool**; it does not create settings next to `llama-server` or beside a GGUF.

## First use

Open **Settings** and choose:

- **llama.cpp Folder** (optional). By default `llama-fit-params` and `llama-bench` are the binaries next to the fixed `llama-server` in `Engines/llama.cpp/build/bin/Release`. Point this folder at a checkout to also discover alternate builds beneath standard `build/bin` locations and known build directories such as `build-mixed`; use **Rescan** and choose the active binaries if several builds are present.
- **Models**, **MMProj**, **Drafters**, and **Chat Template** folders. There is one general Drafters folder; MTP, DFlash, DSpark, and any other external speculative/draft models all live there.
- An existing llama-swap YAML configuration file (needed only for the llama-swap features).

The `llama-server` used by the application is fixed at `Engines/llama.cpp/build/bin/Release/llama-server.exe`, shown read-only under **Detected Tools**, and the default `llama-fit-params` / `llama-bench` are the sibling binaries in that same build folder. Preview, copy, imported commands, test launches, and llama-swap command generation all correspond to that server; for local execution the relative spellings are resolved to absolute paths. There is deliberately no per-command or per-model engine selection.

Every path remains editable. A missing prior path is colored as unavailable rather than silently cleared. Save settings, then return to **Command Builder**; folder-backed selectors refresh from these locations and have individual refresh buttons.

Models are scanned recursively and sorted naturally. The scanner displays `.gguf` files and reduces clear `part-01`/`split-01` multipart GGUF sets to their first loader shard. Template selectors list `.jinja`, `.jinja2`, `.txt`, and `.tmpl`; manual Browse always allows any file.

## Building and testing commands

A new command always begins with the non-removable `-m` model selector. Use **Add Argument** to search aliases, names, and upstream descriptions (`context`, `gpu`, `flash`, `draft`, `template`, and similar terms work). Rows choose appropriate editors for parameterless flags, enums, multi-value flags, models, MMProj files, external draft models, template files, and comma-separated `--spec-type` values. Rows can be reordered by dragging their left-hand grip or with the ↑/↓ buttons; the model row is always first.

The Add Argument picker lists the entire llama.cpp argument catalog. Star a flag to pin it to the top of the picker; pins are personal sort priority only and never change the command, its order, or its values. **Spacer** inserts a visual separator between two argument rows (with up/down/remove controls). Spacers are presentation only: the command, preview, copied text, validation, and llama-swap output are byte-for-byte identical with or without them.

The preview is read-only. **Copy Command** copies a Windows-safe quoted command. **Vertical preview** emits a readable `^`-continued display. The command state—argument order, values, source selections, and model—is saved automatically.

**Test Server** validates the structured command, starts llama-server through `QProcess` (never a shell), then verifies the running process in four stages:

1. **Process** — succeeded only once the process actually emits `started`; start failures report the process error, not a traceback.
2. **Ready** — polls `GET /health` (~0.5 s) while the model loads; `503`/connection-refused keep polling until the configured readiness timeout (Settings, default 180 s, 10–1800 s).
3. **API** — reads `GET /v1/models`; requires a non-empty model list and, when `--alias` is configured, confirms the alias is served.
4. **Inference** — a tiny capability-appropriate probe: `/completion` for generation servers, `/embedding` for embedding-only servers, `/rerank` for reranking servers, and a clear SKIPPED state for modes the verifier does not cover.

The verifier derives the effective host/port/API prefix from the structured command + argument catalog (aliases resolved, `0.0.0.0` verified via loopback, default port 8080, `--api-prefix` honored on every path). `--host` ending in `.sock` or SSL (`--ssl-cert-file` + `--ssl-key-file`) still launch the process but mark the verification as transport-unsupported. An occupied explicit/default port fails before launch so a stale server is never verified. Configured API keys (`--api-key`, `--api-key-file`, or `LLAMA_API_KEY`) are sent as `Authorization: Bearer` and are never shown — the console line masks `--api-key` values.

The summary panel shows each stage live; on success it reports the served model ID, generated text, token counts, throughput, and timings, and the server **keeps running** for manual testing until **Stop** or app close. Failures report the exact failing stage plus the last ~50 lines of real server output. Raw server logs stay in the process console below. `${PORT}` is substituted with an available local port for the run only; the saved command remains unchanged. Stop cancels verification and terminates the process.

The result lives only for the session; run history and comparison are a later phase.

## Memory Test

**Memory Test** runs the active `llama-fit-params` asynchronously against the structured command currently in the builder. It first asks the installed utility which aliases it accepts, then forwards only builder arguments supported by that exact binary. Server-only flags such as `--host` and `--port` are therefore not sent to the fitter.

The first pass uses llama.cpp's `--fit-print on` output for the current requested configuration. The second pass obtains fitted CLI arguments. The result window reports **Model Weights**, **Context / KV Cache**, **Compute Buffers**, per-device/Host rows, GiB primary values with MiB tooltips, fitted arguments, and complete combined stdout/stderr raw output. A successful estimate remains usable even if automatic fitting fails. It never changes the builder unless **Apply Fitted Parameters** is pressed.

Use **Memory Test Options** only to override llama.cpp's normal fit defaults with a free-memory target (`--fit-target`) or minimum fitted context (`--fit-ctx`). Leave both blank to retain the installed llama.cpp defaults. **Cancel Memory Test** terminates an unusually long analysis without freezing the UI.

## Benchmark

**Benchmark** answers "how fast is this exact configuration?" with the installed `llama-bench` binary. The structured server configuration is translated semantically into llama-bench arguments, including device separators, tensor-split separators, GPU-layer representation, and the polarity differences for KV offload, operation offload, and mmap. The run options dialog sets prompt/generation token counts, batch sizes, repetitions, and a warm-up delay.

The results window shows prompt-processing and generation tokens/sec with standard deviation, backend and device information, the exact translated llama-bench arguments, any skipped server-only arguments, warnings about approximations, and the raw `llama-bench` output. These numbers are llama-bench's standalone measurement of the translation/compute pipeline — they are not llama-server or HTTP throughput. (Test Server's inference probe records one small live request's latency and tokens/s; that is a verification signal, not a serving benchmark.)

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

The editors target llama-swap as of the upstream commit recorded in `app/services/llama_swap_service.py` (the bundled `data/llama_swap_config_schema.json` carries the same provenance in its `x-source` field). Field coverage is regression-tested against a checked-in upstream-shaped configuration (`tests/data/upstream_example_minimal.yaml`, trimmed from the upstream `config.example.yaml`). Model entries accept every field of the current upstream model configuration; the general editor covers the current global keys including `logRequests` and `sendLoadingState`. Routing supports both engines: `groups` and `matrix` may coexist under `routing.router.settings`, with `use` selecting the active engine, matching the current upstream runtime. Beyond the bundled schema, the app's semantic layer adds cross-reference checks the schema cannot express: unknown model references, group membership rules, matrix var/eviction-cost references, and scheduler priority entries (model IDs or aliases). Where the upstream schema and the runtime disagree, the stricter schema side wins and the error message names the constraint.

The **llama-swap Config** tab is organized as:

- **Models** — search `models:` entries by ID or name; shows model ID, display name, command, detected model path, and missing-file status. Load a visual-builder-compatible command into the builder, save only the selected entry's `cmd`, edit metadata and the current upstream model-level settings (TTL/unload, stop command, proxy, environment, metadata, macros, filters, timeouts, compat, concurrency limit, send loading state), duplicate, or remove an entry after confirmation. Imported commands with unsupported flags, malformed quoting, or shell syntax stay in clearly labeled **Raw Command Mode** instead of being rewritten unsafely.
- **General, Logging, Profiles** — targeted editors for the remaining common settings.
- **API Keys** — the quick top-level editor for llama-swap `apiKeys`: literal keys stay masked in the list and `${env.NAME}` references are supported.
- **Advanced** — a nested tab holding the seven less-common sections: **Activity / Performance, Macros, Hooks, Upstream, Selectors, Routing, Peers**, each unchanged from its former top-level tab.

Every setting is **presence-aware**: an absent key, an explicitly configured key, and an effective default are shown and handled differently. Displaying a default never writes it; **Reset to Default** removes the key so llama-swap applies its own default, rather than writing the current default value. Unknown fields at any level are surfaced, and a failed validation leaves the file untouched.

YAML edits use `ruamel.yaml` round-trip mode. Each write parses the existing YAML, changes only the targeted subtree, serializes to a sibling temporary file, reparses it, validates against the bundled llama-swap JSON Schema, creates `config.yaml.bak-YYYYMMDD-HHMMSS`, then atomically replaces the original. Updating one model preserves other fields on that model (`ttl`, `aliases`, `env`, and so on), other models, comments as far as round-trip YAML supports them, and unrelated top-level settings. The default backup retention is 10 newest backups.

## Argument catalog

`data/llama_server_flags.json` is a generated offline catalog from llama.cpp's auto-generated `tools/server/README.md`; the UI also has **Catalog → Refresh llama.cpp Arguments**. Refresh downloads and reparses the current upstream README, replacing the bundled copy only after a successful parse. Failure leaves the active bundled catalog untouched.

The documentation is fundamentally prose/Markdown rather than a machine schema. The parser reliably extracts aliases, common value placeholders, braced/bracketed enums, the bare comma-separated `--spec-type` list, and built-in template names. Some complex optional syntaxes — such as llama.cpp's own Hugging Face repository-reference flags (`--hf-repo`, `--hf-token`) and their value grammar — remain plain editable text fields by design.

## Tests

```powershell
cd llama-test-tool
python -m pytest -q tests
```

Tests cover catalog arities/aliases/enums, rendered argv quoting and `${PORT}`, command import, builder persistence and missing-file restoration, preset dialog prefill and emission (including the Custom MTP draft-KV selection), staged server verification against a hermetic fake llama-server (readiness, alias, auth with a sentinel secret, prefix, timing, embedding/rerank/unsupported modes, stop/stale-run safety), round-trip YAML add/update/remove preservation, targeted llama-swap subtree writes (presence-aware globals, routing/model validation), current-upstream llama-swap config shape (model fields, groups+matrix routing coexistence, schema/semantic rejection, bundled schema snapshot provenance), backups, and malformed-YAML refusal.
