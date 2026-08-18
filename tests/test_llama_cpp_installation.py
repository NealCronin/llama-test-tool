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


def test_active_server_is_the_configured_build_mixed_server():
    from app.server import SERVER_COMMAND
    from app.settings import AppSettings

    active = LlamaCppInstallationService.active_server(AppSettings())
    assert active.replace("\\", "/").endswith(SERVER_COMMAND)
