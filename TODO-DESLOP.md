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

### DSLP-9 · P3 · Target admits Linux-only fields on macOS and loose closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45, src/buildlib/elf.py:57 (ELFHeader.kind)
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family, executable_format, and kind are free strings over closed sets. Consumers must re-check is_linux and then trust the optional fields.
- **Recommendation:** Model the macOS and Linux targets as separate types (or a Linux detail record) and the closed sets as Literal types; check consumers in the build repositories before changing the public shape.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

### DSLP-10 · P3 · ELF is_64 and elf_class can only ever be true and 2

- **Area:** Needs owner · Binary inspection
- **Where:** src/buildlib/elf.py:44 (ELFHeader.elf_class), elf.py:73 (is_64), elf.py ELFReport is_64
- **Problem:** _read_header rejects every ELF class except 2, so ELFHeader.elf_class is always 2, is_64 is always True, and inspect() always reports is_64: True. No consumer in cmake-, node-, ffmpeg-, chromium-, python-build, or pi-build reads them (chromium-build has its own ELF reader); only buildlib's tests do. Removing them changes the public API of a released library, so it is a walk-back.
- **Recommendation:** Owner: approve removing elf_class, is_64, and the is_64 report key in the next release, or keep them for report parity with macho.inspect. If approved, delete them and update the two tests.
- **Fix with:** delete
- **Effort:** S

