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

### DSLP-10 · P3 · ELF is_64 and elf_class can only ever be true and 2

- **Area:** Needs owner · Binary inspection
- **Where:** src/buildlib/elf.py:44 (ELFHeader.elf_class), elf.py:73 (is_64), elf.py ELFReport is_64
- **Problem:** _read_header rejects every ELF class except 2, so ELFHeader.elf_class is always 2, is_64 is always True, and inspect() always reports is_64: True. Revalidated 2026-11-15: the original claim that no build repository reads them was wrong. node-build imports buildlib.elf and checks `not header.is_64` (buildsys/validate_product.py:190, a check that can never fire) and asserts header.is_64 in tests/test_elf.py:118; it pins laputa-buildlib==0.1.0, so removal breaks it only when it bumps the pin. python-build reads is_64 only on Mach-O headers (macho.is_64 is meaningful and stays). Removing them changes the public API of a released library, so it is a walk-back.
- **Recommendation:** Owner (re-confirm, since the earlier approval assumed no consumers): remove elf_class, ELFHeader.is_64 and the ELF report is_64 key, and in the same campaign drop node-build`s dead `not header.is_64` ELF check and its test assertion before it bumps to the release that removes them. Or keep them for report parity with macho.inspect.
- **Fix with:** delete
- **Effort:** S

