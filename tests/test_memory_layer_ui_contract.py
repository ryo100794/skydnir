import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app/src/main/kotlin/io/github/ryo100794/pdocker/MainActivity.kt"
STRINGS = ROOT / "app/src/main/res/values/strings.xml"
STRINGS_JA = ROOT / "app/src/main/res/values-ja/strings.xml"


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


class MemoryLayerUiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = MAIN.read_text()
        cls.strings = STRINGS.read_text()
        cls.strings_ja = STRINGS_JA.read_text()

    def test_memory_layers_live_on_overview_not_debug_resources(self):
        overview = function_body(self.main, "renderOverview")
        debug = function_body(self.main, "renderDebugResources")
        self.assertIn("renderMemoryLayerVisualization()", overview)
        self.assertLess(
            overview.index("renderMemoryLayerVisualization()"),
            overview.index("renderDaemonOperations()"),
            "overview should show memory state before daemon/job controls",
        )
        self.assertNotIn("renderMemoryLayerVisualization()", debug)

    def test_os_pdocker_footprint_is_reported_with_percentages(self):
        for needle in [
            "pdockerProcessCount",
            "pdockerVmSize",
            "pdockerRss",
            "pdockerSwap",
            "pdockerMemoryFootprint()",
            "memory_layers_pdocker_share_fmt",
            "pdocker.RSS.percent_of_RAM",
            "pdocker.VmSwap.percent_of_used_swap",
        ]:
            self.assertIn(needle, self.main)
        self.assertIn("percent_of_RAM", self.main)
        self.assertIn("percent_of_used_swap", self.main)
        self.assertIn("pdocker footprint inside OS", self.strings)
        self.assertIn("OS 内で pdocker が占める量", self.strings_ja)

    def test_graph_labels_separate_scales_instead_of_comparable_full_width_rows(self):
        view = self.main[self.main.index("private class MemoryLayerView") :]
        self.assertIn("scaleTotal: Long", view)
        self.assertIn("scaleLabel: String", view)
        self.assertIn("drawLaneGroup", view)
        self.assertIn("each row has its own scale", view)
        self.assertIn("do not compare width to swap/app/pager rows", view)
        self.assertIn("lanes are not stacked", view)
        self.assertNotIn("globalScaleBytes", view)
        self.assertNotRegex(
            view,
            re.compile(r"segment\\.bytes[^\\n]+/\\s*total\\.toDouble\\(\\)"),
            "segment widths must not silently normalize every row to its own total without labels",
        )

    def test_pdocker_is_visually_separated_inside_ram_and_swap(self):
        view = self.main[self.main.index("private class MemoryLayerView") :]
        for needle in [
            'Segment("pdocker RSS"',
            'Segment("other used"',
            'Segment("pdocker swap"',
            "0xff58ffd2",
            "Guest memory illusion",
            "Android keeps",
            "headroom",
        ]:
            self.assertIn(needle, view)


    def test_pager_artifact_is_labeled_as_past_selftest_with_age_and_status(self):
        for needle in [
            "past self-test",
            "artifactCreatedAtEpoch",
            "artifactStatus",
            "artifactAgeSeconds",
            "created_at_epoch",
            "formatArtifactAge",
            "memory_layers_artifact_summary_fmt",
            "not live /proc",
        ]:
            self.assertIn(needle, self.main + self.strings)

    def test_transparent_artifact_fields_flow_to_snapshot_summary_and_details(self):
        for needle in [
            "transparentLastMmapLen",
            "transparentPendingAfterEntry",
            "transparentMaxResidentPages",
            "transparentBytesIn",
            "transparentBytesOut",
            "transparentDirtyPageOuts",
            "last_mmap_len",
            "pending_after_entry",
            "max_resident_pages",
            "bytes_in",
            "bytes_out",
            "dirty_page_outs",
        ]:
            self.assertIn(needle, self.main)
        for needle in [
            "transparent mmap",
            "bytes in/out",
            "dirty outs",
            "max resident pages",
        ]:
            self.assertIn(needle, self.strings + self.strings_ja)

    def test_pager_selftest_action_and_artifact_fields_are_visible(self):
        for needle in [
            "runMemoryPagerSelfTest",
            "--pdocker-memory-pager-managed-poc",
            "--pdocker-memory-pager-transparent-poc",
            "page_ops_per_sec",
            "guest-visible reserve",
            "resident window",
            "backing",
        ]:
            self.assertIn(needle, self.main + self.strings)

    def test_overview_has_plain_virtual_memory_pager_status_card(self):
        render = function_body(self.main, "renderMemoryLayerVisualization")
        for needle in [
            "widget_virtual_memory_pager",
            "memoryPagerValue(snapshot)",
            "memoryPagerDetail(snapshot)",
            "memoryPagerState(snapshot)",
            "memoryPagerAmplification(snapshot)",
        ]:
            self.assertIn(needle, render + self.main)
        for needle in [
            "Virtual memory pager",
            "guest %2$s / resident %3$s",
            "headroom",
            "page ops",
            "bytes in/out",
            "SIGSEGV stops",
        ]:
            self.assertIn(needle, self.strings)
        for needle in [
            "仮想記憶 pager",
            "稼働中",
            "待機中",
            "未使用",
        ]:
            self.assertIn(needle, self.strings_ja)

    def test_pager_card_does_not_claim_live_workload_from_stale_selftest_only(self):
        state = function_body(self.main, "memoryPagerState")
        detail = function_body(self.main, "memoryPagerDetail")
        self.assertIn("managedPageIns + snapshot.managedPageOuts + snapshot.transparentSigsegvStops", state)
        self.assertIn("transparentRegistered", state)
        self.assertIn("source %8$s age %9$s; %10$s", self.strings)
        self.assertIn("artifactPresent", self.main)
        self.assertIn("snapshot.source", detail)
        self.assertIn("formatArtifactAge", detail)
        self.assertIn("memoryPagerTelemetryNote(snapshot)", detail)
        self.assertIn("past self-test artifact only", self.strings)
        self.assertIn("not live workload telemetry", self.strings)

    def test_hierarchy_connectors_and_non_overlapping_layout_are_explicit(self):
        view = self.main[self.main.index("private class MemoryLayerView") :]
        for needle in [
            "drawHierarchyConnector",
            "process subset",
            "pager wraps selected mappings",
            "dp(720)",
            "ellipsizeForWidth",
            "val footerY = y + 22f * density",
        ]:
            self.assertIn(needle, self.main if needle == "dp(720)" else view)

    def test_app_virtual_memory_metrics_expose_availability_or_na_reason(self):
        for needle in [
            "appVmMetricsAvailable",
            "appVmMetricsReason",
            "App-level virtual memory metrics: N/A",
            "app.vm_metrics.reason",
            "N/A: /proc/",
            "not exposed",
            "available from /proc/",
            "app Vm metrics",
        ]:
            self.assertIn(needle, self.main + self.strings)


if __name__ == "__main__":
    unittest.main()
