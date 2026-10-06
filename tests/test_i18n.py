import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "web" / "locales"
PROXMENUX_LANGUAGES = {"en", "de", "es", "fr", "it", "pt", "sk", "sv"}


class I18nTests(unittest.TestCase):
    def test_catalogs_match_proxmenux_languages_and_source_keys(self):
        files = {path.stem for path in LOCALES.glob("*.json")}
        self.assertEqual(files, PROXMENUX_LANGUAGES)
        catalogs = {
            language: json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
            for language in PROXMENUX_LANGUAGES
        }
        source_keys = set(catalogs["es"])
        self.assertGreater(len(source_keys), 150)
        for language, catalog in catalogs.items():
            self.assertEqual(set(catalog), source_keys, language)
            self.assertTrue(all(isinstance(value, str) and value.strip() for value in catalog.values()), language)


if __name__ == "__main__":
    unittest.main()
