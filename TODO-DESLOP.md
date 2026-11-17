Prefix: DSLP
Next: 12

Deslop campaign backlog ([plan](https://github.com/laputa-systems/stuff/blob/main/docs/campaigns/deslop.md)). One agent works this file: take the lowest-numbered open ticket whose area does not start with `Needs owner ·`. After DSLP-5, work the open P1 and P2 findings, then fill in `## Report` below.

## Report

Not started.

## Tickets

### DSLP-4 · P2 · Tests: coverage, boundary gaps, mutation

- **Area:** deslop · tests
- **Where:** tests/
- **Problem:** All four named critical-module Scenarios are qualified with every survivor dispositioned: ELF509 (469 killed,40 survivors), Mach-O401 (383 killed,18), release331 (317 killed,14), locks388 (378 killed,10). Exact identities and controls are in docs/deslop-{elf,macho,release,locks}-contracts.md. Package-wide branch reporting now executes 900/901 statements and291/292 branches over all eight first-party files, including subprocesses; the one negative ELF read-range guard is unreachable through public readers and documented without an exclusion. make coverage, all207 native tests and lint pass; warmed suite7.48s vs201-test6.45s. The repeated parser910 identities match prior result classes exactly. The whole-change selection contract, make mutants-diff and a qualified CI mutation lane remain unfinished.
- **Recommendation:** Finish whole-change selection and result accounting, with changed tests/config selecting the critical-scope union and explicit negative controls. Qualify the bounded lane before relying on it. Tools are mirrored; workflow changes still require the owner's separate authorization. Keep coverage informational and preserve module Scenario evidence. Do not restart a Full package sweep without an owner request. Accept only after the remaining deslop Tests selection/lane requirements pass.
- **Fix with:** deslop
- **Effort:** M



### DSLP-9 · P3 · Target admits Linux-only fields on macOS and free strings for its closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family and executable_format are free strings over closed sets (the module defines MACOS and LINUX_MUSL). Consumers must re-check is_linux and then trust the optional fields. Out of scope here: ELFHeader.kind (elf.py:57) is open-ended by design, returning type-{etype} for any ELF type it does not name, so it is not a closed set and must keep that behavior.
- **Recommendation:** Revalidate against the build repositories first: list each consumer of Target and its Linux fields (repomod deps). Then model the Linux details as one record present exactly on Linux targets, and family and executable_format as Literal types, keeping the public attribute names or filing the rename as a walk-back. Leave ELFHeader.kind a str.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

