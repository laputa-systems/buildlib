Prefix: DSLP
Next: 10

Deslop campaign backlog ([plan](https://github.com/laputa-systems/stuff/blob/main/docs/campaigns/deslop.md)). One agent works this file: take the lowest-numbered open ticket whose area does not start with `Needs owner ·`. After DSLP-5, work the open P1 and P2 findings, then fill in `## Report` below.

## Report

Not started.

## Tickets

### DSLP-4 · P2 · Tests: coverage, boundary gaps, mutation

- **Area:** deslop · tests
- **Where:** tests/
- **Problem:** The suite is behavior-named and boundary-shaped; whether it detects parser and validation defects is unmeasured.
- **Recommendation:** Run the deslop Tests step with mutmut over locks, release, elf, and macho. Accept: no undispositioned survivor in the critical scope, the test cost budget holds, and the mutation lane is green.
- **Fix with:** deslop
- **Effort:** M

### DSLP-5 · P2 · Dead code: tools and debloat

- **Area:** deslop · dead code
- **Where:** whole repository
- **Problem:** No dead-code scan has run. ELFHeader.is_64 and inspect()["is_64"] are always True because _read_header rejects every other class.
- **Recommendation:** Run the deslop Dead code step. Accept: tools clean or allowlisted with reasons; public API removals go to an owner ticket.
- **Fix with:** deslop
- **Effort:** S

### DSLP-6 · P3 · Mach-O string readers leak struct.error on a short load command

- **Area:** Binary inspection
- **Where:** src/buildlib/macho.py:232-258 (dependencies, dylib_id, rpaths), macho.py:220 (build_version)
- **Problem:** load_commands accepts cmdsize 8, an empty payload, and the lc_str readers then call struct.unpack_from on it. Reproduced: an image with an empty LC_RPATH makes rpaths() and inspect() raise struct.error instead of MachOError, so a caller that handles malformed images by catching MachOError crashes instead.
- **Recommendation:** Check each command's minimum payload size in one place before unpacking and raise MachOError; add the empty-payload case to test_rejects_load_commands_that_run_past_the_header.
- **Fix with:** error-model
- **Effort:** S

### DSLP-9 · P3 · Target admits Linux-only fields on macOS and loose closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45, src/buildlib/elf.py:57 (ELFHeader.kind)
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family, executable_format, and kind are free strings over closed sets. Consumers must re-check is_linux and then trust the optional fields.
- **Recommendation:** Model the macOS and Linux targets as separate types (or a Linux detail record) and the closed sets as Literal types; check consumers in the build repositories before changing the public shape.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

