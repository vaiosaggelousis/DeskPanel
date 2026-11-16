"""Public-distribution checks: repository secrets + wake screen."""
from pathlib import Path
import json, unittest
ROOT=Path(__file__).resolve().parents[1]
class ReleaseTests(unittest.TestCase):
 def test_no_runtime_config_or_access_key(self):
  self.assertFalse((ROOT/'config.json').exists())
  cfg=json.loads((ROOT/'config.example.json').read_text())
  self.assertNotIn('key',cfg)
 def test_wake_overlay_removed(self):
  html=(ROOT/'index.html').read_text()
  js=(ROOT/'static/app.js').read_text()
  self.assertNotIn('id="dim"',html)
  self.assertNotIn("$('#dim')",js)
  self.assertNotIn('dim-clock',js)
 def test_config_excluded_from_git(self):
  self.assertIn('config.json',(ROOT/'.gitignore').read_text())
 def test_previous_key_migration_available(self):
  self.assertIn('"DeskPanel-v6"',(ROOT/'server.py').read_text())
