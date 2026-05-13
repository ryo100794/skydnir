from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app/src/main/kotlin/io/github/ryo100794/pdocker/MainActivity.kt"
STRINGS = ROOT / "app/src/main/res/values/strings.xml"


def test_service_probe_success_is_requested_until_listener_proof():
    main = MAIN.read_text()
    strings = STRINGS.read_text()
    assert "ServiceContainerProof" in main
    assert "projectRunningServiceProofs(projectDir, snapshots)" in main
    assert "matches.distinctBy { it.engineContainerId }.singleOrNull()" in main
    assert "service_health_requested" in strings
    assert "listener proof missing" in strings
    assert "getString(R.string.service_health_requested_with_http_fmt, httpStatus)" in main
    assert 'val httpStatus = if (code in 200..399) "HTTP $code" else "down HTTP $code"' in main


def test_container_cards_do_not_trust_persisted_running_without_engine_snapshot():
    main = MAIN.read_text()
    body = main.split("private fun containerIsRunning", 1)[1].split("private fun containerEngineIdKeys", 1)[0]
    assert "containerSnapshotIsRunning(snapshot)" in body
    assert "current Engine truth" in body
    assert 'optBoolean("Running", false) == true' not in body
    assert "return false" in body
