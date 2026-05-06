#!/usr/bin/env python3
"""Fine-grained offline checks for terminal/editor contract details."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "main": ROOT / "app/src/main/kotlin/io/github/ryo100794/pdocker/MainActivity.kt",
    "editor": ROOT / "app/src/main/kotlin/io/github/ryo100794/pdocker/CodeEditorView.kt",
    "terminal": ROOT / "app/src/main/assets/xterm/index.html",
    "bridge": ROOT / "app/src/main/kotlin/io/github/ryo100794/pdocker/Bridge.kt",
    "runtime": ROOT / "app/src/main/python/pdockerd_bridge.py",
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def require(name: str, cond: bool) -> None:
    ok(name) if cond else fail(name)


def main() -> int:
    source = {k: v.read_text() for k, v in FILES.items()}

    require("compose runtime-blocker fallback message is present", "Build blocked by current container runtime" in source["main"])
    require("compose fallback container state is explicit", "Prepared for inspection (container runtime unavailable)" in source["main"])
    require("host shell moved into diagnostics", "action_host_shell" in source["main"] and "renderDiagnostics" in source["main"])

    require("terminal selection menu has all action buttons", 'data-selection-action="all"' in source["terminal"] and 'data-selection-action="copy"' in source["terminal"] and 'data-selection-action="clear"' in source["terminal"])
    require("terminal selection menu prevents click-through", "selectionMenu.addEventListener('click'" in source["terminal"] and "event.stopPropagation()" in source["terminal"])
    require("selection markers are visible and refreshed", ".selection-status.active" in source["terminal"] and "selectionStatus.textContent" in source["terminal"])
    require("selection menu touch events stop propagation", "selectionMenu.addEventListener(name" in source["terminal"] and "event.stopPropagation()" in source["terminal"])
    require("pinch zoom updates term font size", "touches.length === 2" in source["terminal"] and "setFontSize" in source["terminal"])
    require("terminal touch scroll uses viewport pixels", "viewportEl()" in source["terminal"] and "viewport.scrollTop -= deltaY" in source["terminal"] and "touchScrollThreshold" in source["terminal"])
    require("terminal body drag does not resize selection", "nearestSelectionHandle" not in source["terminal"] and "selectionDrag = roleForVisualHandle" in source["terminal"])
    require("terminal key toggles remain visible", 'data-toggle="select"' in source["terminal"] and 'data-toggle="ctrl"' in source["terminal"] and 'data-toggle="alt"' in source["terminal"])
    require("modifier toggle state propagates", "btn.classList.toggle('active', !!mods" in source["terminal"])
    require("ime fallback suppresses duplicate terminal data", "suppressTerminalDataOnce" in source["terminal"] and "consumeSuppressedTerminalData(data)" in source["terminal"] and "suppressTerminalDataOnce(event.data)" in source["terminal"] and "suppressTerminalDataOnce(event.key)" in source["terminal"])
    require("terminal selection suppresses ime", "suppressImeForSelection" in source["terminal"] and "selectionSuppressesIme()" in source["terminal"] and "inputmode', 'none'" in source["terminal"])
    require("readonly selection actions keep ime suppressed", "runSelectionAction" in source["terminal"] and "if (readOnly) suppressImeForSelection()" in source["terminal"] and "if (readOnly || selectionSuppressesIme()) suppressImeForSelection()" in source["terminal"])
    require("terminal starts bridge-owned initial command", "fun startInitial()" in source["bridge"] and "PdockerBridge.startInitial()" in source["terminal"] and "PdockerBridge.start(PdockerBridge.initialCommand())" not in source["terminal"])

    require("editor exposes visible whitespace transformer", "VisibleWhitespaceTransformation" in source["editor"])
    require("editor supports indent mode toggle", "toggleIndentMode" in source["editor"] and "convertIndentation(editor.text.toString()" in source["editor"])
    require("editor supports line-aware indent/outdent", "transformSelectedLines" in source["editor"] and "indentSelection" in source["editor"] and "outdentSelection" in source["editor"])
    require("editor handles line numbering", "lineNumbers" in source["editor"] and "updateLineNumbers()" in source["editor"])
    require("editor has pinch zoom", "ScaleGestureDetector" in source["editor"] and "editorFontSize" in source["editor"])

    require("bridge advertises no-proot fallback", "setdefault(\"PDOCKER_RUNTIME_BACKEND\", \"no-proot\")" in source["runtime"])
    require("daemon sets Docker tmp paths", "PDOCKER_TMP_DIR" in source["runtime"] and "PROOT_TMP_DIR" in source["runtime"])

    require("host intent start path still gates daemon start", "ACTION_SMOKE_START" in source["main"] and "startDaemon()" in source["main"])
    require("dockerfile/compose commands avoid host shell path", "docker compose up --detach --build" in source["main"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
