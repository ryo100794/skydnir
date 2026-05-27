# Skydnir Newsflow

Snapshot date: 2026-05-04.

## Purpose

This document is the operating workflow for keeping GitHub-facing project
content current without turning publicity into a second source of truth. The
rule is simple: durable facts live in `docs/`; public surfaces summarize and
link back to those facts.

## Scope

- Present Skydnir as an attractive, practical Android container
  workbench.
- Keep public updates honest about the current experimental state.
- Make releases, issues, README highlights, Wiki pages, and demos tell the
  same story.
- Give testers a clear path for useful reports.
- Let one publicity lead or agent coordinate updates without owning app code.

## Canonical Sources

Use these documents before publishing claims:

| Claim type | Canonical source |
|---|---|
| Current product status | `docs/plan/STATUS.md` |
| Active unfinished work | `docs/plan/TODO.md` |
| Docker compatibility | `docs/test/COMPATIBILITY.md` and `docs/test/compat-audit-latest.md` |
| GPU bridge design and limits | `docs/design/GPU_COMPAT.md` |
| Docker scope and Android limits | `docs/design/DOCKER_COMPAT_SCOPE.md` |
| User-facing workspace flow | `docs/manual/DEFAULT_DEV_WORKSPACE.md` |
| Public wording kit | `docs/manual/PROMOTION.md` |

When facts differ, fix or cite the relevant `docs/` page before updating
GitHub-facing copy. Public-message wording belongs in
[`PROMOTION.md`](PROMOTION.md); this page owns the publishing workflow.

## What To Publish

Publish updates in small, named categories so readers can tell what changed:

- **Release**: publish when a signed APK or tagged build is available. Include
  highlights, compatibility, device testing, known limits, and links to docs.
- **Compatibility snapshot**: publish when Engine, Compose, archive, TTY, or
  runtime behavior changes. Include supported endpoints, tested commands, gaps,
  and exact Android device context.
- **Demo**: publish when a workflow is visually convincing end-to-end. Include
  the clip, APK version, device, steps shown, and known-limits caption.
- **Tester call**: publish when a narrow workflow needs outside devices.
  Include APK/version, steps, required logs, and where to reply.
- **GPU bridge note**: publish when benchmarks, bridge transport, ICD/shim, or
  llama mode changes. Include CPU fallback status, forced GPU status,
  benchmark artifact, and blocker.
- **Template note**: publish when VS Code, Continue, Codex, Claude Code,
  llama.cpp, or project-library behavior changes. Include template name, ports,
  start path, expected result, and known caveats.
- **Limit note**: publish when a Docker feature is unsupported or
  platform-bound. Include what users asked for, the Android reason, and the
  current workaround or next step.

Do not announce generic "Docker on Android" parity. Prefer precise phrases
such as "Docker-compatible Engine API subset", "Compose workspace UI", "direct
Android executor smoke path", and "GPU bridge experiment".

## Where To Publish

- **GitHub release notes**: canonical public changelog for tagged builds.
- **Pinned issues**: living threads for roadmap, compatibility, and device
  testing.
- **README highlights**: short summary only. Keep detailed claims in `docs/`.
- **GitHub Wiki mirror**: reader-friendly copies of stable docs, with links
  back to the repository files.
- **Discussions or social posts**: short updates that link to issues, releases,
  or docs.

Every public update should link to at least one durable repo artifact: a doc,
release, issue, benchmark artifact, or compatibility report.

## Cadence

- **Per merged feature**: add or refresh the relevant doc note before public
  amplification.
- **Twice weekly while active**: update pinned issue comments with progress,
  blockers, and next tester request.
- **Per APK build**: publish release notes or a prerelease note, even if the
  build is experimental.
- **Per demo**: record a reproducible checklist and link to the doc section
  that explains limits.
- **Monthly**: prune stale README/Wiki highlights and close or retitle old
  tester calls.

Skip public hype when the only change is internal cleanup with no user-visible
effect. Mention it in release notes if it affects reliability, size, startup,
storage, or compatibility.

## Release Notes

Use the template in `docs/manual/PROMOTION.md`. Keep these sections consistent:

- **Highlights**: three to six user-visible improvements.
- **Compatibility**: Engine API, Compose, direct executor, TTY/logs, storage,
  GPU bridge, and templates.
- **Device testing**: device model, Android version, APK flavor, smoke path,
  benchmark or log artifact.
- **Known limits**: Android platform restrictions and unsupported Docker
  features.
- **Security/signing**: signing status, secret audit status, redaction notes
  when applicable.

Use "confirmed on" language for device-specific results. Example:
"Confirmed on Android 15 SOG15" is better than "works on Android".

## Demo Checklist

Before recording:

- Build or install the APK version named in the demo.
- Start pdockerd from the app UI.
- Confirm `_ping`, image pull, Compose up, and log streaming on the target
  device.
- Clear old containers or explain why they are visible.
- Check free storage and battery restrictions.
- Prepare a known-limits caption.

During the demo:

- Show the native upper/lower split UI.
- Start Compose from the UI.
- Show live job cards and persistent logs.
- Open a container card, service URL, and file browser.
- Open a PTY-backed interactive terminal or exec session.
- Show VS Code Server on `127.0.0.1:18080` when the default workspace is the
  subject.
- Show llama.cpp only in the mode being claimed: CPU fallback for ordinary
  template demos, forced Vulkan/OpenCL only when benchmark artifacts are shown.
- Show storage metrics or prune when discussing large images.

After publishing:

- Add the link to the relevant pinned issue or release.
- Capture any tester confusion as a doc fix.
- Avoid reusing old clips after runtime, port, template, or GPU behavior has
  changed.

## Tester Call Flow

1. Pick one workflow per call: default workspace, image pull, Compose up,
   container terminal, archive/copy, llama CPU fallback, or GPU bridge
   benchmark.
2. State the APK version, expected Android range, and whether root, Termux, or
   adb is required. For normal tester calls, the answer should be "no root, no
   Termux-first shell".
3. Ask testers to report device model, Android version, ABI, free storage, APK
   flavor, install source, and battery restriction state.
4. Provide exact actions and expected evidence: screenshot, log excerpt,
   `docker ps`, service URL result, benchmark JSON, or compatibility gap.
5. Triage replies within two business days into works, platform limit, pdocker
   bug, docs confusion, or needs reproduction.
6. Move confirmed findings to the relevant `docs/` page, then summarize back
   into the issue thread.
7. Close the call when the question is answered, or retitle it with the next
   narrow ask.

## Publicity Lead Operating Model

Yes, appoint a lightweight publicity lead. This can be one person or one agent,
but it should be an explicit role because the project has many fast-moving
surfaces.

The lead owns coordination, not truth. Their job is to keep GitHub-facing text
fresh while making `docs/` the canonical record.

Responsibilities:

- Watch merged changes, test artifacts, and current status docs.
- Draft release notes from `docs/manual/PROMOTION.md` and this workflow.
- Post concise issue-thread updates for roadmap, compatibility, and tester
  calls.
- Propose README highlight changes to the main agent, with links to the docs
  source for each claim.
- Mirror stable docs to the Wiki and mark mirrors with source file and snapshot
  date.
- Keep social/demo wording honest about Android limits and experimental GPU
  bridge status.
- Ask maintainers for confirmation before publishing claims that are not in
  `docs/`.

Non-responsibilities:

- Do not edit app code, workflows, scripts, or project-library templates from
  this role.
- Do not invent compatibility promises to make release notes sound better.
- Do not treat issue comments, README text, or Wiki mirrors as canonical when
  they conflict with `docs/`.

## README And Wiki Sync

The publicity lead may maintain a "README highlight proposal" in an issue or
PR comment, but the source claims should remain in docs. The generated showcase
pages are refreshed with:

```sh
python3 scripts/update-showcase.py
```

GitHub Actions checks the generated files on pull requests and refreshes
`docs/showcase/` on `main`, scheduled runs, and manual dispatches. Wiki
publication is optional: set the repository variable `SKYDNIR_SYNC_WIKI=1`
or the temporary compatibility variable `PDOCKER_SYNC_WIKI=1`
after enabling the GitHub Wiki to let the workflow copy exactly these generated
pages into the Wiki repository:

- `docs/showcase/WIKI_HOME.md` -> `Home.md`
- `docs/showcase/PROJECT_DASHBOARD.md` -> `Project-Dashboard.md`
- `docs/showcase/ROADMAP_TIMELINE.md` -> `Roadmap-Timeline.md`

A safe manual sync pass is:

1. Read `docs/plan/STATUS.md`, `docs/test/COMPATIBILITY.md`,
   `docs/design/DOCKER_COMPAT_SCOPE.md`, and `docs/manual/PROMOTION.md`.
2. Draft a five-bullet highlight update with one doc link per bullet.
3. Ask the main agent or maintainer to apply README changes.
4. Add or update any extra manual/design Wiki pages by hand only after the repo
   docs are merged; the workflow does not publish those pages automatically.
5. Put this footer on mirrored Wiki pages:

```text
Source: docs/... in ryo100794/skydnir
Snapshot: YYYY-MM-DD
If this page differs from the repository docs, the repository docs win.
```

## Issue Thread Updates

Use this compact format for pinned issue updates:

```markdown
### Update YYYY-MM-DD

**Changed**
- ...

**Confirmed**
- Device:
- Android:
- APK/build:
- Evidence:

**Known limits**
- ...

**Next tester ask**
- ...

Docs: ...
```

Keep issue updates factual and short. Link to logs or benchmark artifacts
instead of pasting long output.

## Honest Language Cheatsheet

Prefer:

- "Docker-compatible Android APK"
- "Engine API-compatible subset"
- "Compose/Dockerfile workspace UI"
- "direct Android executor smoke path"
- "known Android platform limits"
- "GPU bridge experiment"
- "CPU fallback mode"
- "confirmed on this device/build"

Avoid:

- "full Docker on Android"
- "Docker Desktop replacement"
- "complete GPU passthrough"
- "works on all Android devices"
- "BuildKit support" unless a tested BuildKit-compatible path exists
- "NVIDIA Docker equivalent" for Android devices

## Done Criteria For Public Updates

A public update is ready when:

- The claim appears in a current `docs/` file or release artifact.
- The update names the APK/build or says it is unreleased work.
- Device-specific results include device and Android version.
- Unsupported Docker features are described plainly.
- Tester asks include exact evidence to collect.
- README or Wiki mirrors link back to repo docs.

## Maintenance

- Keep this page focused on publishing workflow and coordination.
- Keep reusable wording in [`PROMOTION.md`](PROMOTION.md).
- Update canonical-source links here when status, compatibility, or design
  ownership moves.
