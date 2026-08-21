from app.services.llama_cpp_installation import LlamaCppInstallationService


def touch(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_discovers_standard_posix_build_tools(tmp_path):
    server = touch(tmp_path, "build/bin/llama-server")
    fit = touch(tmp_path, "build/bin/llama-fit-params")
    bench = touch(tmp_path, "build/bin/llama-bench")
    installation = LlamaCppInstallationService.discover(tmp_path)
    assert installation.server.paths == (server.resolve(),)
    assert installation.fit_params.paths == (fit.resolve(),)
    assert installation.bench.paths == (bench.resolve(),)


def test_discovers_windows_release_tools(tmp_path):
    server = touch(tmp_path, "build/bin/Release/llama-server.exe")
    fit = touch(tmp_path, "build/bin/Release/llama-fit-params.exe")
    bench = touch(tmp_path, "build/bin/Release/llama-bench.exe")
    installation = LlamaCppInstallationService.discover(tmp_path)
    assert installation.server.paths == (server.resolve(),)
    assert installation.fit_params.paths == (fit.resolve(),)
    assert installation.bench.paths == (bench.resolve(),)


def test_discovers_multiple_builds_and_handles_missing_tools(tmp_path):
    debug = touch(tmp_path, "build/bin/Debug/llama-server.exe")
    release = touch(tmp_path, "build-vulkan/bin/Release/llama-server.exe")
    installation = LlamaCppInstallationService.discover(tmp_path)
    assert installation.server.paths[0] == release.resolve()
    assert debug.resolve() in installation.server.paths
    assert not installation.fit_params.found
    assert not installation.bench.found


def test_active_server_is_the_standard_build_server():
    from app.server import SERVER_COMMAND, server_executable_path
    from app.settings import AppSettings

    assert SERVER_COMMAND == "Engines/llama.cpp/build/bin/Release/llama-server.exe"
    active = LlamaCppInstallationService.active_server(AppSettings())
    assert active.replace("\\", "/").endswith(SERVER_COMMAND)
    assert server_executable_path().as_posix().endswith("Engines/llama.cpp/build/bin/Release/llama-server.exe")


def _settings(tmp_path, fit="", bench="", folder=""):
    from app.settings import AppSettings

    return AppSettings(llama_fit_params_executable=fit, llama_bench_executable=bench, llama_cpp_folder=folder)


def test_active_fit_and_bench_default_to_fixed_build_folder(tmp_path, monkeypatch):
    import app.services.llama_cpp_installation as installation_module
    fit = touch(tmp_path, "Engines/llama.cpp/build/bin/Release/llama-fit-params.exe")
    bench = touch(tmp_path, "Engines/llama.cpp/build/bin/Release/llama-bench.exe")

    monkeypatch.setattr(installation_module, "fixed_tool_path", lambda tool: {
        "llama-fit-params": fit, "llama-bench": bench,
    }[tool])
    settings = _settings(tmp_path)  # no selection, no llama.cpp folder
    assert LlamaCppInstallationService.active_fit_params(settings) == str(fit)
    assert LlamaCppInstallationService.active_bench(settings) == str(bench)


def test_active_fit_and_bench_prefer_explicit_selection_over_fixed(tmp_path, monkeypatch):
    import app.services.llama_cpp_installation as installation_module

    fixed_fit = touch(tmp_path, "Engines/build/bin/Release/llama-fit-params.exe")
    fixed_bench = touch(tmp_path, "Engines/build/bin/Release/llama-bench.exe")
    selected_fit = touch(tmp_path, "build-mixed/bin/llama-fit-params.exe")
    selected_bench = touch(tmp_path, "build-mixed/bin/llama-bench.exe")
    monkeypatch.setattr(installation_module, "fixed_tool_path", lambda tool: {
        "llama-fit-params": fixed_fit, "llama-bench": fixed_bench,
    }[tool])
    settings = _settings(tmp_path, fit=str(selected_fit), bench=str(selected_bench))
    assert LlamaCppInstallationService.active_fit_params(settings) == str(selected_fit)
    assert LlamaCppInstallationService.active_bench(settings) == str(selected_bench)


def test_active_fit_and_bench_fall_back_to_discovery_without_fixed(tmp_path, monkeypatch):
    import app.services.llama_cpp_installation as installation_module

    missing = tmp_path / "nowhere" / "llama-fit-params.exe"
    monkeypatch.setattr(installation_module, "fixed_tool_path", lambda tool: missing)
    discovered_bench = touch(tmp_path, "build/bin/llama-bench")
    settings = _settings(tmp_path, folder=str(tmp_path))
    assert LlamaCppInstallationService.active_fit_params(settings) == ""
    assert LlamaCppInstallationService.active_bench(settings) == str(discovered_bench)
