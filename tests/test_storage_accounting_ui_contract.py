import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app/src/main/kotlin/io/github/ryo100794/pdocker/MainActivity.kt"
STRINGS = ROOT / "app/src/main/res/values/strings.xml"


def function_body(src: str, name: str) -> str:
    marker = f"private fun {name}"
    start = src.index(marker)
    brace = src.index("{", start)
    depth = 0
    for pos in range(brace, len(src)):
        if src[pos] == "{":
            depth += 1
        elif src[pos] == "}":
            depth -= 1
            if depth == 0:
                return src[start : pos + 1]
    raise AssertionError(f"function body not closed: {name}")


class StorageAccountingUiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = MAIN.read_text(encoding="utf-8")
        cls.strings = STRINGS.read_text(encoding="utf-8")

    def test_overview_reconciles_physical_categories_without_shared_layer_double_counting(self):
        collect = function_body(self.main, "collectStorageMetrics")
        for needle in [
            "val layerUsage = account(layerRoot)",
            "val imageUsage = account(imageRoot)",
            "val containerUsage = account(containerRoot)",
            "excludeInodes = accountedInodes",
            "val otherUsage = diskUsage(pdockerHome, excludeInodes = accountedInodes)",
            "imagesPhysicalBytes",
            "cacheBytes = cacheUsage.bytes",
            "logsBytes = logUsage.bytes",
            "metadataBytes = metadataUsage.bytes",
            "otherBytes = otherUsage.bytes",
        ]:
            self.assertIn(needle, collect)
        self.assertRegex(
            self.main,
            re.compile(r"val reconciledBytes: Long get\(\) = imagesPhysicalBytes \+ containerPrivateBytes \+ cacheBytes \+ logsBytes \+ metadataBytes \+ otherBytes"),
        )

    def test_images_tab_declares_logical_scope_and_duplicate_shared_layer_delta(self):
        render_images = function_body(self.main, "renderImages")
        scope = function_body(self.main, "renderImageStorageScope")
        self.assertIn("renderImageStorageScope(imageInfos)", render_images)
        for needle in [
            "logicalViewBytes",
            "uniqueReferencedLayerBytes",
            "duplicateSharedBytes",
            "widget_image_storage_scope",
            "image_storage_scope_detail_fmt",
        ]:
            self.assertIn(needle, scope)
        self.assertIn("Images tab lists per-image logical view size", self.strings)
        self.assertIn("Shared layers are stored once physically", self.strings)

    def test_reconciliation_is_visible_and_logged_with_checksum(self):
        render = function_body(self.main, "renderStorageMetrics")
        refresh = function_body(self.main, "refreshStorageMetricsAsync")
        checksum = function_body(self.main, "storageReconciliationChecksum")
        for needle in [
            "storage_reconcile_fmt",
            "reconciliationChecksum",
            "reconciliationDeltaBytes",
            "formatBytes(metrics.imagesPhysicalBytes)",
        ]:
            self.assertIn(needle, render)
        self.assertIn("Log.i", refresh)
        self.assertIn("storage reconcile checksum=", refresh)
        self.assertIn("CRC32", checksum)
        self.assertIn("total %1$s = images %2$s + containers %3$s + cache %4$s + logs %5$s + metadata %6$s + other %7$s", self.strings)

    def test_overview_exposes_top_storage_consumers_for_large_total_debugging(self):
        collect = function_body(self.main, "collectStorageMetrics")
        top = function_body(self.main, "topStorageConsumers")
        render = function_body(self.main, "renderStorageMetrics")
        for needle in [
            "topConsumers = topStorageConsumers()",
            "widget_storage_top_consumers",
            "storage_top_consumer_row_fmt",
            "metrics.topConsumers.first().bytes",
        ]:
            self.assertIn(needle, collect + render)
        for label in [
            '"layers"',
            '"images"',
            '"containers"',
            '"tmp"',
            '"build-cache"',
            '"logs"',
            '"metadata"',
            '"projects"',
            '"workspaces"',
            '"models"',
            '"volumes"',
            '"documents-mirror"',
            '"document-volumes"',
        ]:
            self.assertIn(label, top)
        self.assertIn("excludeInodes = accountedInodes", top)
        self.assertIn("isUnderPdockerHome(file)", top)
        self.assertIn("relativePdockerPath(file)", top)
        self.assertIn("Top app-private storage consumers", self.strings)
        self.assertIn("allocated-block groups", self.strings)

    def test_documents_mount_scope_is_annotated_but_not_removed_from_other(self):
        collect = function_body(self.main, "collectStorageMetrics")
        self.assertIn("documentsStorageRootForAccounting", collect)
        self.assertIn("keep it in \"other\"", collect)
        self.assertIn("documentsBytes = documentsUsage.bytes", collect)
        self.assertIn("storage_documents_scope_external_fmt", self.main + self.strings)

    def test_overview_wording_distinguishes_app_private_physical_from_image_logical_views(self):
        self.assertIn("app-private allocated pdocker", self.strings)
        self.assertIn("physical allocated app-private", self.strings)
        self.assertIn("Images tab is logical image-layer views", self.strings)
        self.assertIn("projects/workspaces/models/volumes/document mirrors", self.strings)


if __name__ == "__main__":
    unittest.main()
