Prefix: DSLP
Next: 12

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

### DSLP-11 · P2 · Mach-O payload readers ignore big-endian image headers

- **Area:** Binary inspection
- **Where:** src/buildlib/macho.py:174,221,227,239,249,258; tests/test_macho.py::test_payload_fields_follow_image_byte_order
- **Problem:** Assumed P2. At ba50980, both supported big-endian Mach-O magics decode headers and command tables correctly, but build-version fields and dylib/rpath offsets are always unpacked little-endian. An independent eight-case public fixture covers both byte orders, 32/64-bit headers and modern/legacy version commands: original code passes four little-endian cases and fails all four big-endian cases with wrong version/platform values. Valid big-endian string offsets also decode to out-of-command values. The fixture uses CPU type 18 for big-endian images and preserves raw command payload bytes. No production incident is claimed.
- **Recommendation:** Retain the header byte order through a private command parser and use it for numeric payload fields. Keep the public LoadCommand shape, raw payload bytes and command order unchanged, without rereading the image header for each command. Retain the eight-case regression and qualify affected consumers before merging.
- **Fix with:** root-cause
- **Effort:** M


### DSLP-9 · P3 · Target admits Linux-only fields on macOS and free strings for its closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family and executable_format are free strings over closed sets (the module defines MACOS and LINUX_MUSL). Consumers must re-check is_linux and then trust the optional fields. Out of scope here: ELFHeader.kind (elf.py:57) is open-ended by design, returning type-{etype} for any ELF type it does not name, so it is not a closed set and must keep that behavior.
- **Recommendation:** Revalidate against the build repositories first: list each consumer of Target and its Linux fields (repomod deps). Then model the Linux details as one record present exactly on Linux targets, and family and executable_format as Literal types, keeping the public attribute names or filing the rename as a walk-back. Leave ELFHeader.kind a str.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

