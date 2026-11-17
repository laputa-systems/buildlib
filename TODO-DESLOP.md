Prefix: DSLP
Next: 12

Deslop campaign backlog ([plan](https://github.com/laputa-systems/stuff/blob/main/docs/campaigns/deslop.md)). One agent works this file: take the lowest-numbered open ticket whose area does not start with `Needs owner ·`. After DSLP-5, work the open P1 and P2 findings, then fill in `## Report` below.

## Report

Not started.

## Tickets

### DSLP-4 · P2 · Tests: coverage, boundary gaps, mutation

- **Area:** deslop · tests
- **Where:** tests/
- **Problem:** Historical whole-package discovery produced 1,682 mutants; that earlier inventory is not current qualification. Current mirrored mutmut 3.8.1 Scenarios qualify all four named critical modules: ELF509 (469 killed,40 dispositioned survivors), Mach-O401 (383 killed,18 dispositioned), release331 (317 killed,14 dispositioned), and locks388 (378 killed,10 dispositioned). Exact inventories, per-survivor rationale, fault controls and native costs are in docs/deslop-{elf,macho,release,locks}-contracts.md. Package-wide branch coverage reporting and a qualified whole-change mutation lane remain unfinished.
- **Recommendation:** Finish the deslop Tests package-wide branch report and disposition every unrun decision region. Qualify whole-change selection and negative controls before depending on a mutation lane; tools are mirrored now, but CI changes require the owner's separate authorization. Accept: the critical-module survivor, coverage, cost and lane requirements are met. Preserve these bounded module results; do not restart a Full package sweep without an owner request.
- **Fix with:** deslop
- **Effort:** M



### DSLP-9 · P3 · Target admits Linux-only fields on macOS and free strings for its closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family and executable_format are free strings over closed sets (the module defines MACOS and LINUX_MUSL). Consumers must re-check is_linux and then trust the optional fields. Out of scope here: ELFHeader.kind (elf.py:57) is open-ended by design, returning type-{etype} for any ELF type it does not name, so it is not a closed set and must keep that behavior.
- **Recommendation:** Revalidate against the build repositories first: list each consumer of Target and its Linux fields (repomod deps). Then model the Linux details as one record present exactly on Linux targets, and family and executable_format as Literal types, keeping the public attribute names or filing the rename as a walk-back. Leave ELFHeader.kind a str.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

