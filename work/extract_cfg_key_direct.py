import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work" / ".venv" / "Lib" / "site-packages"))

package = types.ModuleType("wechatauto")
package.__path__ = [str(ROOT / "work" / "wechatauto-replica" / "wechatauto")]
sys.modules["wechatauto"] = package
logger_module = types.ModuleType("wechatauto.logger")
logger_module.wxlog = logging.getLogger("wechatauto")
sys.modules["wechatauto.logger"] = logger_module

# The cfg extractor only reads process memory. Stub the crypto imports so this
# diagnostic can run with the surviving Python 3.13 runtime after Python 3.12
# was removed from the machine.
crypto_ciphers = types.ModuleType("cryptography.hazmat.primitives.ciphers")
crypto_ciphers.Cipher = object
crypto_ciphers.algorithms = types.SimpleNamespace()
crypto_ciphers.modes = types.SimpleNamespace()
for name in (
    "cryptography",
    "cryptography.hazmat",
    "cryptography.hazmat.primitives",
):
    sys.modules[name] = types.ModuleType(name)
sys.modules["cryptography.hazmat.primitives.ciphers"] = crypto_ciphers

db_path = ROOT / "work" / "wechatauto-replica" / "wechatauto" / "db.py"
spec = importlib.util.spec_from_file_location("wechatauto.db", db_path)
module = importlib.util.module_from_spec(spec)
sys.modules["wechatauto.db"] = module
spec.loader.exec_module(module)

for pid in (50948, 44980):
    print(pid, module.extract_master_key_from_cfg(pid))
