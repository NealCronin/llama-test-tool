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
