"""Static checks for the v6 media control center."""
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
class MediaUITests(unittest.TestCase):
    def test_media_controls_and_presets_present(self):
        html=(ROOT/'index.html').read_text(encoding='utf-8')
        for required in ('id="mute"','id="volume"','id="media-source"','id="output-menu"','data-volume="25"','data-volume="50"','data-volume="75"','data-volume="100"'):
            self.assertIn(required,html)
        self.assertEqual(html.count('data-media='),3)
        self.assertIn('id="play-icon"',html)
    def test_application_source_recorded(self):
        server=(ROOT/'server.py').read_text(encoding='utf-8')
        js=(ROOT/'static/app.js').read_text(encoding='utf-8')
        self.assertIn('source_app_user_model_id',server)
        self.assertIn('sourceName(m&&m.source)',js)
    def test_key_migration_from_v5(self):
        server=(ROOT/'server.py').read_text(encoding='utf-8')
        self.assertIn('"DeskPanel-v5"',server)
if __name__=='__main__': unittest.main()
