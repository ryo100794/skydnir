import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STRING_FILES = [
    ROOT / "app" / "src" / "main" / "res" / "values" / "strings.xml",
    ROOT / "app" / "src" / "main" / "res" / "values-ja" / "strings.xml",
]


class SkydnirAndroidUiCopyTest(unittest.TestCase):
    def strings(self, path: Path) -> dict[str, str]:
        root = ET.parse(path).getroot()
        return {
            item.attrib["name"]: "".join(item.itertext())
            for item in root.findall("string")
        }

    def test_visible_android_labels_use_skydnir_brand(self):
        english = self.strings(STRING_FILES[0])

        self.assertEqual("Skydnir", english["app_name"])
        self.assertEqual("skydnird", english["pdockerd_notification_channel"])
        self.assertEqual("skydnird", english["pdockerd_notification_title"])
        self.assertIn("Skydnir daemon", english["pdockerd_notification_text"])

    def test_user_visible_string_values_do_not_expose_legacy_brand(self):
        # Resource names and package paths intentionally remain unchanged in
        # this phase. The guard is for user-visible copy only.
        legacy_tokens = ("pdocker", "pdockerd", "PDocker", "pDocker")
        for path in STRING_FILES:
            with self.subTest(path=path):
                strings = self.strings(path)
                offenders = {
                    name: value
                    for name, value in strings.items()
                    if any(token in value for token in legacy_tokens)
                }
                self.assertEqual({}, offenders)


if __name__ == "__main__":
    unittest.main()
