"""Check runtime imports that cross the historical repository/package boundary."""
import ast
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackageMetadataTests(unittest.TestCase):
    def test_runtime_root_module_imports_are_shipped_in_wheel(self):
        config = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        shipped = set(config['tool']['setuptools'].get('py-modules', []))
        root_modules = {p.stem for p in ROOT.glob('*.py')}
        required = set()
        for path in (ROOT / 'gfi_prototype').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    required.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    required.add(node.module.split('.')[0])
        self.assertLessEqual(required & root_modules, shipped)
