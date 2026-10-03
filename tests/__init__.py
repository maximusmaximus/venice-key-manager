# Venice Key Manager Tests
"""
Test-suite sandbox.

Importing the ``tests`` package (which ``python -m unittest tests/test_x.py`` and
``python -m unittest discover`` both do) redirects every side effect into a
throw-away temp directory *before* any test module imports ``core``:

* ``VENICE_VAULT_PATH``           -> temp copy of the vault (never the live vault)
* ``VENICE_SUPERVISOR_STATE_DIR`` -> temp dir (restart buttons no longer restart
                                     the live supervisor)
* ``core.deployer`` agent config map -> temp YAML copies (no writes into real
                                     agent configs)

Set ``VENICE_TESTS_USE_LIVE=1`` to opt out (not recommended).
"""

import os
import sys
import atexit
import shutil
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SANDBOX_DIR = None  # type: ignore

if os.environ.get("VENICE_TESTS_USE_LIVE") != "1" and not os.environ.get("VENICE_TEST_SANDBOX"):
    SANDBOX_DIR = Path(tempfile.mkdtemp(prefix="venice_tests_"))
    os.environ["VENICE_TEST_SANDBOX"] = str(SANDBOX_DIR)

    sandbox_vault = SANDBOX_DIR / "venice_vault.json"
    live_vault = PROJECT_ROOT / "venice_vault.json"
    if live_vault.exists():
        # Copy so tests that expect a configured vault keep working, while the
        # live file is never written by the test-suite.
        shutil.copy2(str(live_vault), str(sandbox_vault))
        shutil.copy2(str(live_vault), str(SANDBOX_DIR / "venice_vault.backup.json"))
    os.environ["VENICE_VAULT_PATH"] = str(sandbox_vault)

    state_dir = SANDBOX_DIR / "supervisor"
    state_dir.mkdir(parents=True, exist_ok=True)
    os.environ["VENICE_SUPERVISOR_STATE_DIR"] = str(state_dir)

    try:
        import core.deployer as _deployer

        agents_dir = SANDBOX_DIR / "agent_configs"
        agents_dir.mkdir(parents=True, exist_ok=True)
        new_map = {}
        for name in list(_deployer.AGENT_CONFIG_MAP.keys()):
            p = agents_dir / f"{name}.yaml"
            p.write_text("model:\n  api_key: placeholder\n", encoding="utf-8")
            new_map[name] = p
        _deployer.AGENT_CONFIG_MAP.clear()
        _deployer.AGENT_CONFIG_MAP.update(new_map)
        default_cfg = agents_dir / "config.yaml"
        default_cfg.write_text("model:\n  api_key: placeholder\n", encoding="utf-8")
        _deployer.DEFAULT_CONFIG_PATH = default_cfg
    except Exception as _e:  # pragma: no cover
        print(f"[tests] warning: could not sandbox deployer: {_e}", file=sys.stderr)

    def _cleanup(path=SANDBOX_DIR):
        shutil.rmtree(str(path), ignore_errors=True)

    atexit.register(_cleanup)
