import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_GRADLE = ROOT / "app" / "build.gradle.kts"
MAIN_ACTIVITY = ROOT / "app" / "src" / "main" / "kotlin" / "io" / "github" / "ryo100794" / "pdocker" / "MainActivity.kt"


class BuildMetadataContractTest(unittest.TestCase):
    def test_apk_build_metadata_is_generated_at_build_time(self):
        gradle = APP_GRADLE.read_text(encoding="utf-8")

        self.assertIn('nonBlankEnv("SKYDNIR_BUILD_TIME_UTC", "PDOCKER_BUILD_TIME_UTC")', gradle)
        self.assertIn("DateTimeFormatter.ISO_INSTANT.format(pdockerBuildInstant)", gradle)
        self.assertIn('gitOutput("git", "rev-parse", "--short=12", "HEAD")', gradle)
        self.assertIn('nonBlankEnv("SKYDNIR_BUILD_COMMIT", "PDOCKER_BUILD_COMMIT")', gradle)
        self.assertIn('nonBlankEnv("SKYDNIR_BUILD_NUMBER", "PDOCKER_BUILD_NUMBER")', gradle)

        default_config = re.search(
            r"defaultConfig \{(?P<body>.*?)\n    \}",
            gradle,
            re.S,
        )
        self.assertIsNotNone(default_config)
        body = default_config.group("body")
        self.assertIn('buildConfigField("String", "BUILD_TIME_UTC", buildConfigString(pdockerBuildTimeUtc))', body)
        self.assertIn('buildConfigField("String", "BUILD_GIT_COMMIT", buildConfigString(pdockerBuildCommit))', body)
        self.assertIn('buildConfigField("String", "BUILD_NUMBER", buildConfigString(pdockerBuildNumber))', body)
        self.assertNotIn('buildConfigString(pdockerVersionValue("buildTimeUtc"))', body)
        self.assertNotIn('buildConfigString(pdockerVersionValue("buildCommit"))', body)
        self.assertNotIn('buildConfigString(pdockerVersionValue("buildNumber"))', body)

    def test_ui_uses_buildconfig_metadata_not_manual_literal(self):
        activity = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertIn("BuildConfig.BUILD_TIME_UTC", activity)
        self.assertIn("BuildConfig.BUILD_GIT_COMMIT", activity)
        self.assertIn("R.string.app_build_info_fmt", activity)
        self.assertNotIn("2026-05-05T23:20:33Z", activity)


if __name__ == "__main__":
    unittest.main()
