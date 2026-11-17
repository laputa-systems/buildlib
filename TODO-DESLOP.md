Prefix: DSLP
Next: 12

Deslop campaign backlog ([plan](https://github.com/laputa-systems/stuff/blob/main/docs/campaigns/deslop.md)). One agent works this file: take the lowest-numbered open ticket whose area does not start with `Needs owner ·`. After DSLP-5, work the open P1 and P2 findings, then fill in `## Report` below.

## Report

Not started.

## Tickets

### DSLP-4 · P2 · Tests: coverage, boundary gaps, mutation

- **Area:** deslop · tests
- **Where:** tests/
- **Problem:** Historical whole-package discovery produced 1,682 mutants; that earlier inventory is not a current qualification. The current mirrored mutmut 3.8.1 module Scenarios now qualify ELF (509: 469 killed, 40 individually dispositioned survivors) and Mach-O (401: 383 killed, 18 individually dispositioned survivors). See docs/deslop-elf-contracts.md and docs/deslop-macho-contracts.md for identities, controls, configuration and native costs. Locks and release critical modules, package-wide branch coverage and a qualified whole-change mutation lane remain unfinished.
- **Recommendation:** Continue bounded locks and release Scenarios using the mirrored tools; disposition every selected survivor and prove new boundary tests with injected faults. Add package-wide branch coverage reporting and qualify whole-change selection with negative controls before depending on a mutation lane. CI changes require the owner's separate authorization. Accept: no undispositioned critical survivor and the deslop Tests coverage/cost/lane acceptance is met. Do not rerun a Full package sweep without an owner request.
- **Fix with:** deslop
- **Effort:** M



### DSLP-9 · P3 · Target admits Linux-only fields on macOS and free strings for its closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family and executable_format are free strings over closed sets (the module defines MACOS and LINUX_MUSL). Consumers must re-check is_linux and then trust the optional fields. Out of scope here: ELFHeader.kind (elf.py:57) is open-ended by design, returning type-{etype} for any ELF type it does not name, so it is not a closed set and must keep that behavior.
- **Recommendation:** Revalidate against the build repositories first: list each consumer of Target and its Linux fields (repomod deps). Then model the Linux details as one record present exactly on Linux targets, and family and executable_format as Literal types, keeping the public attribute names or filing the rename as a walk-back. Leave ELFHeader.kind a str.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

