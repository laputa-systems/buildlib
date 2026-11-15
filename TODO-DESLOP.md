Prefix: DSLP
Next: 11

Deslop campaign backlog ([plan](https://github.com/laputa-systems/stuff/blob/main/docs/campaigns/deslop.md)). One agent works this file: take the lowest-numbered open ticket whose area does not start with `Needs owner ·`. After DSLP-5, work the open P1 and P2 findings, then fill in `## Report` below.

## Report

Not started.

## Tickets

### DSLP-4 · P2 · Tests: coverage, boundary gaps, mutation

- **Area:** deslop · tests
- **Where:** tests/
- **Problem:** mutmut 3.8.1 (scratch install from upstream PyPI; not mirrored) over src/buildlib: 1682 mutants in about 66s. Before this step 235 survived and 10 had no covering test (is_elf); the first tests (W^X hardening, SHA256SUMS separators, optional lock fields, size 0) brought survivors to 216. They concentrate in elf._read_header (19), elf.build_id_ranges (15), macho._string_at (12), elf._dynamic_entries (12), elf._dyn_string (12), macho.signature_status (11), macho.signature_metadata_ranges (10), macho.load_commands (9), elf._read_dynamic_entries (9), locks._download (8). Many are error-message text mutations; verified equivalent: read_text encoding utf-8 versus the 3.15 default, and sorting by os.fsencode versus str order.
- **Recommendation:** Run the deslop Tests step recipe (references/tools.md, Python) on a scratch copy, disposition each remaining survivor (message text needs no test; give each parser boundary a byte-built malformed image), add make coverage, and record the before and after counts. No CI mutation lane until mutmut is mirrored. Accept: no undispositioned survivor in locks, release, elf, or macho.
- **Fix with:** deslop
- **Effort:** M

### DSLP-9 · P3 · Target admits Linux-only fields on macOS and free strings for its closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family and executable_format are free strings over closed sets (the module defines MACOS and LINUX_MUSL). Consumers must re-check is_linux and then trust the optional fields. Out of scope here: ELFHeader.kind (elf.py:57) is open-ended by design, returning type-{etype} for any ELF type it does not name, so it is not a closed set and must keep that behavior.
- **Recommendation:** Revalidate against the build repositories first: list each consumer of Target and its Linux fields (repomod deps). Then model the Linux details as one record present exactly on Linux targets, and family and executable_format as Literal types, keeping the public attribute names or filing the rename as a walk-back. Leave ELFHeader.kind a str.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M


