"""Package and resource paths must survive the repository reorganization."""
import pkgutil
import subprocess
import sys
import tempfile
import unittest
from importlib import import_module
from pathlib import Path

import backend
from backend.paths import PROJECT_ROOT
from backend.server import ROOT
from backend.analysis.landcover_services import SOURCES
from backend.catalog.data_catalog_services import AGRICULTURE_REFERENCE_MANIFEST
from backend.core.map_services import SOURCE_DIR
from backend.core.search_services import PLACE_FILE


class ProjectLayoutTest(unittest.TestCase):
    def test_resources_still_resolve_to_repository_not_package_directory(self):
        self.assertEqual(ROOT, PROJECT_ROOT)
        self.assertEqual(SOURCES, PROJECT_ROOT / 'data_sources')
        self.assertEqual(SOURCE_DIR, PROJECT_ROOT / 'map_sources')
        for path in (AGRICULTURE_REFERENCE_MANIFEST, PLACE_FILE, ROOT / 'dist/index.html'):
            self.assertTrue(path.is_file(), str(path))

    def test_every_backend_module_can_be_imported_from_the_package(self):
        for item in pkgutil.walk_packages(backend.__path__, backend.__name__ + '.'):
            with self.subTest(module=item.name):
                import_module(item.name)

    def test_root_entry_help_works_outside_repository_without_creating_data(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(PROJECT_ROOT / 'server.py'), '--help'],
                                    cwd=directory, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--db', result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])
