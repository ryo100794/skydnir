# Git hook scripts

This directory contains local collaboration hooks installed by
[`../setup-git-worktree.sh`](../setup-git-worktree.sh). These files are not
stable public entrypoints; they are implementation details for a configured
worktree.

| Hook | Installed by | Role |
|---|---|---|
| `prepare-commit-msg` | `scripts/setup-git-worktree.sh` | Appends `Pdocker-Machine` and `Pdocker-Branch` trailers unless they are already present. |

Local machine identity remains untracked. The hook reads `pdocker.machineId`
from Git config or `.git/info/pdocker-machine-id`; do not commit generated
machine-id state.

See [`../../docs/manual/GIT_COLLABORATION.md`](../../docs/manual/GIT_COLLABORATION.md)
for the collaboration workflow.
