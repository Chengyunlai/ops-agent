from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

repository_root = Path(SPECPATH).parent
cli_source = repository_root / "apps/cli/src"
harness_source = repository_root / "packages/harness/src"
config_template = cli_source / "ops_agent_cli/resources/config.toml"

hidden_imports = collect_submodules("langchain_openai")
metadata = []
for distribution in (
    "kubernetes",
    "langchain",
    "langchain-core",
    "langchain-openai",
    "langgraph",
    "textual",
):
    metadata.extend(copy_metadata(distribution))

analysis = Analysis(
    [str(cli_source / "ops_agent_cli/__main__.py")],
    pathex=[str(cli_source), str(harness_source)],
    binaries=[],
    datas=[
        *metadata,
        (str(config_template), "ops_agent_cli/resources"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
python_modules = PYZ(analysis.pure)

executable = EXE(
    python_modules,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="ops-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="ops-agent",
)
