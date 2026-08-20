from pathlib import Path

from app.services import flag_catalog
from app.services.flag_catalog import FlagCatalog

def catalog() -> FlagCatalog:
    return FlagCatalog.parse_readme("""\
| `--jinja, --no-jinja` | whether to use jinja |\n
| `-c, --ctx-size N` | size of the prompt context |\n
| `-fa, --flash-attn [on\\|off\\|auto]` | Flash Attention |\n
| `--control-vector-layer-range START END` | layer range |\n
| `--perf, --no-perf` | performance timing |\n
| `--spec-type none,draft-mtp,draft-dflash,ngram-mod` | comma-separated types |\n
""")


def test_parameterless_positive_negative_aliases_are_one_spec():
    spec = catalog().find("--no-jinja")
    assert spec.canonical_name == "--jinja"
    assert spec.parameter_count == 0
    assert "--no-jinja" in spec.aliases


def test_one_value_enum_and_aliases():
    spec = catalog().find("-fa")
    assert spec.canonical_name == "--flash-attn"
    assert spec.parameter_count == 1
    assert spec.choices == ("on", "off", "auto")


def test_multi_value_flag():
    spec = catalog().find("--control-vector-layer-range")
    assert spec.parameter_count == 2
    assert spec.parameter_names == ("START", "END")


def test_bare_documented_choice_list_becomes_value_editor():
    spec = catalog().find("--spec-type")
    assert spec.parameter_count == 1
    assert spec.choices == ("none", "draft-mtp", "draft-dflash", "ngram-mod")

def test_single_argv_grammars_do_not_split_internal_commas_or_spaces():
    actual = FlagCatalog.parse_readme("""\
| `-dev, --device <dev1,dev2,..>` | comma-separated device list |
| `-ts, --tensor-split N0,N1,N2,...` | comma-separated proportions |
| `-fitt, --fit-target MiB0,MiB1,MiB2,...` | comma-separated targets |
| `-Cr, --cpu-range lo-hi` | CPU range |
| `-ot, --override-tensor <tensor name pattern>=<buffer type>,...` | override tensor |
""")
    for name in ("--device", "--tensor-split", "--fit-target", "--cpu-range", "--override-tensor"):
        spec = actual.find(name)
        assert spec.parameter_count == 1
    assert actual.find("--tensor-split").choices == ()
    assert actual.find("--fit-target").choices == ()


def test_negative_aliases_retain_their_polarity():
    spec = catalog().find("--no-perf")
    assert spec.positive_aliases == ("--perf",)
    assert spec.negative_aliases == ("--no-perf",)

def test_bundled_catalog_retains_real_current_server_grammars():
    bundled = FlagCatalog.load_bundled(Path("data/llama_server_flags.json"))
    assert len(bundled.specs) > 200
    assert bundled.find("--device").parameter_count == 1
    assert bundled.find("--tensor-split").parameter_count == 1
    assert bundled.find("--fit-target").parameter_count == 1
    assert bundled.find("--override-tensor").parameter_count == 1
    assert bundled.find("--tensor-split").choices == ()


def test_negative_only_flag_remains_selectable():
    actual = FlagCatalog.parse_readme("| `--no-mmap` | disable memory mapping |")
    spec = actual.find("--no-mmap")
    assert spec.preferred_name == "--no-mmap"
    assert spec.selectable_aliases == ("--no-mmap",)


def test_catalog_exposes_every_flag_without_common_policy():
    actual = FlagCatalog.parse_readme("""\
| `-c, --ctx-size N` | context |
| `--new-upstream-flag VALUE` | unknown |
| `--port PORT` | server port |
""")
    assert not hasattr(flag_catalog, "COMMON_FLAG_REFERENCES")
    assert not hasattr(actual, "common_specs")
    assert {spec.canonical_name for spec in actual.search("")} == {"--ctx-size", "--new-upstream-flag", "--port"}


def test_real_catalog_short_negative_aliases_are_negative():
    bundled = FlagCatalog.load_bundled(Path("data/llama_server_flags.json"))
    for alias in ("-nkvo", "-nr", "-ndio"):
        spec = bundled.find(alias)
        assert spec is not None
        assert spec.is_negative(alias)
