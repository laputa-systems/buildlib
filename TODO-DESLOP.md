Prefix: DSLP
Next: 12

Deslop campaign backlog ([plan](https://github.com/laputa-systems/stuff/blob/main/docs/campaigns/deslop.md)). One agent works this file: take the lowest-numbered open ticket whose area does not start with `Needs owner ·`. After DSLP-5, work the open P1 and P2 findings, then fill in `## Report` below.

## Report

Ground, Diagnose, Types, Tests and Dead code passed. See the
[verification commands](AGENTS.md#verification),
[coverage record](docs/deslop-coverage.md) and
[mutation gate receipts](docs/deslop-mutation-ci.md).
The Tests slice changes development tooling and tests. The shipped wheel
kept its previous size and SHA-256. DSLP-9 remains open.

Cumulative review covered `6ef86b9..a749bba` and found that the public
`write_manifest` could use different product values for its filename and
body when a mapping changed between reads. A changing mapping now
regresses this case. Both outputs use the same snapshot.

The local suite passed 208 tests and Ruff and ty passed. With the candidate
`src` first on `PYTHONPATH`, consumer release tests passed in node-build
`5deebd8` (10), chromium-build `05b05f7` (3), ffmpeg-build `539141d` (5),
python-build `b1d8240f7` (1) and pi-build `442f463` (3). The test interpreter
checked that `buildlib.release.__file__` resolved to the candidate source.
The PR records the unittest selections.

Retained CI at `a749bba` passed 17 checks across macOS arm64 and Linux
x86-64 and arm64. All four hosted negative controls are linked from
[#26](https://github.com/laputa-systems/buildlib/pull/26).
The wheel version and archive format were outside the cumulative repair.

## Tickets




### DSLP-9 · P3 · Target admits Linux-only fields on macOS and free strings for its closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family and executable_format are free strings over closed sets (the module defines MACOS and LINUX_MUSL). Consumers must re-check is_linux and then trust the optional fields. Out of scope here: ELFHeader.kind (elf.py:57) is open-ended by design, returning type-{etype} for any ELF type it does not name, so it is not a closed set and must keep that behavior.
- **Recommendation:** Revalidate against the build repositories first: list each consumer of Target and its Linux fields (repomod deps). Then model the Linux details as one record present exactly on Linux targets, and family and executable_format as Literal types, keeping the public attribute names or filing the rename as a walk-back. Leave ELFHeader.kind a str.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

