Prefix: DSLP
Next: 16

Deslop campaign backlog ([plan](https://github.com/laputa-systems/stuff/blob/main/docs/campaigns/deslop.md)). One agent works this file: take the lowest-numbered open ticket whose area does not start with `Needs owner ·`. After DSLP-5, work the open P1 and P2 findings, then fill in `## Report` below.

## Report

**Bench**

Audit: no benchmarks or timing harness exist. `make test` exercises small
correctness fixtures; the mutation gate's clock enforces a deadline. Neither
measures runtime performance. There is no benchmark entrypoint, preflight,
baseline, sample policy or performance gate to validate, and no stale gate
was found. No Edit, Change, Scenario or Full workload was run; Full is manual
only. No timing claim is made on this busy host.

Outcome list, ranked by missed-regression risk (all currently uncovered):

1. Release archive creation and checksum generation/verification: elapsed and
   CPU time, peak memory, I/O and artifact size, with completed bytes and
   archive/checksum correctness (DSLP-12).
2. Verified download and cached input verification: elapsed time, throughput,
   CPU, memory, I/O and bandwidth, with completed bytes and pinned digest and
   size (DSLP-13).
3. Installed-tree ELF/Mach-O discovery, inspection and normalization, including
   macOS signature verification: elapsed and CPU time, memory and I/O, with
   file/byte counts and correct reports and normalized bytes (DSLP-14).
4. Lock/target lookup and manifest generation, including Python startup, affect
   per-build latency; these once-per-input or asset operations have no workload
   or oracle at representative scale. Include them when choosing workloads in
   DSLP-15 rather than assuming their impact.

Across outcomes there is no recorded process scope, statistic, sample count,
host validity, attribution or regression anchor. DSLP-15 covers operability
and attribution: start with separate CPU sampling, allocation/peak RSS and
I/O or syscall counts, recording instrumentation overhead outside timed runs.
The smallest next action is a representative release-tree workload with an
output oracle, then a tiered entrypoint when a harness exists. All four gaps
are P3; no performance values or gates were inferred from correctness tests.

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

### DSLP-12 · P3 · Release packaging has no representative performance workload

- **Area:** deslop · bench
- **Where:** src/buildlib/release.py:155-197, src/buildlib/release.py:213-230, tests/test_release.py:153-208
- **Problem:** Archive creation, checksum generation and verification can dominate per-asset CPU, memory and I/O; small correctness fixtures do not measure representative release trees, artifact size or completed work, so regressions have no baseline.
- **Recommendation:** Add a pinned representative release-tree workload checking archive contents, determinism and checksums, recording completed bytes, CPU, wall time, peak memory, I/O and archive size with interleaved baseline comparisons.
- **Fix with:** bench-pyramid
- **Effort:** M

### DSLP-13 · P3 · Verified input fetching has no representative performance workload

- **Area:** deslop · bench
- **Where:** src/buildlib/locks.py:212-250, tests/test_locks.py:272-296
- **Problem:** Download and cached verification paths process whole source inputs, but correctness tests do not establish their throughput, CPU, memory or I/O at representative sizes; slowdowns can escape detection.
- **Recommendation:** Measure a pinned realistic local input on download and cache-hit paths with digest and size oracles and completed-byte counts; isolate network variability and compare interleaved CPU and resource measurements.
- **Fix with:** bench-pyramid
- **Effort:** M

### DSLP-14 · P3 · Binary inspection and normalization have no representative performance workload

- **Area:** deslop · bench
- **Where:** src/buildlib/elf.py:253-266, src/buildlib/elf.py:326-345, src/buildlib/macho.py:289-314, src/buildlib/macho.py:348-365
- **Problem:** Scanning, inspecting and normalizing installed binaries can multiply per-file work and memory with image count and size; byte-built correctness tests do not measure a representative tree or completed work.
- **Recommendation:** Measure pinned representative ELF and Mach-O trees with inspection and normalization correctness oracles, file and byte counts, CPU, peak memory and I/O; cover codesign separately on macOS.
- **Fix with:** bench-pyramid
- **Effort:** M

### DSLP-15 · P3 · Performance comparisons lack an attribution and operating path

- **Area:** deslop · bench
- **Where:** Makefile:1-26, AGENTS.md:37, scripts/mutants_gate.py:140-156
- **Problem:** There is no benchmark registry, preflight, reference comparison, run metadata, regression gate or attribution recipe; the mutation deadline and past test-suite timings are not runtime performance measurements. Scope, sample count and host validity therefore cannot be established for consumer outcomes.
- **Recommendation:** When representative workloads exist, add the bench-pyramid tiered entrypoint and preflight, pinned inputs and run metadata, interleaved reference comparison with busy-host handling, and separate low-overhead CPU sampling, allocation/RSS and I/O or syscall attribution recipes.
- **Fix with:** bench-pyramid
- **Effort:** M

