Prefix: BLIB
Next: 10

### BLIB-5 · P3 · Target admits Linux-only fields on macOS and free strings for its closed sets

- **Area:** Domain model
- **Where:** src/buildlib/targets.py:20-45
- **Problem:** Target carries docker_platform, alpine_arch, musl_loader, and elf_machine as optional fields that are always set on Linux and never on macOS, and family and executable_format are free strings over closed sets (the module defines MACOS and LINUX_MUSL). Consumers must re-check is_linux and then trust the optional fields. Out of scope here: ELFHeader.kind (elf.py:57) is open-ended by design, returning type-{etype} for any ELF type it does not name, so it is not a closed set and must keep that behavior.
- **Recommendation:** Revalidate against the build repositories first: list each consumer of Target and its Linux fields (repomod deps). Then model the Linux details as one record present exactly on Linux targets, and family and executable_format as Literal types, keeping the public attribute names or filing the rename as a walk-back. Leave ELFHeader.kind a str.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M

### BLIB-6 · P3 · Release packaging has no representative performance workload

- **Area:** bench
- **Where:** src/buildlib/release.py:155-197, src/buildlib/release.py:213-230, tests/test_release.py:153-208
- **Problem:** Archive creation, checksum generation and verification can dominate per-asset CPU, memory and I/O; small correctness fixtures do not measure representative release trees, artifact size or completed work, so regressions have no baseline.
- **Recommendation:** Add a pinned representative release-tree workload checking archive contents, determinism and checksums, recording completed bytes, CPU, wall time, peak memory, I/O and archive size with interleaved baseline comparisons.
- **Fix with:** bench-pyramid
- **Effort:** M

### BLIB-7 · P3 · Verified input fetching has no representative performance workload

- **Area:** bench
- **Where:** src/buildlib/locks.py:212-250, tests/test_locks.py:272-296
- **Problem:** Download and cached verification paths process whole source inputs, but correctness tests do not establish their throughput, CPU, memory or I/O at representative sizes; slowdowns can escape detection.
- **Recommendation:** Measure a pinned realistic local input on download and cache-hit paths with digest and size oracles and completed-byte counts; isolate network variability and compare interleaved CPU and resource measurements.
- **Fix with:** bench-pyramid
- **Effort:** M

### BLIB-8 · P3 · Binary inspection and normalization have no representative performance workload

- **Area:** bench
- **Where:** src/buildlib/elf.py:253-266, src/buildlib/elf.py:326-345, src/buildlib/macho.py:289-314, src/buildlib/macho.py:348-365
- **Problem:** Scanning, inspecting and normalizing installed binaries can multiply per-file work and memory with image count and size; byte-built correctness tests do not measure a representative tree or completed work.
- **Recommendation:** Measure pinned representative ELF and Mach-O trees with inspection and normalization correctness oracles, file and byte counts, CPU, peak memory and I/O; cover codesign separately on macOS.
- **Fix with:** bench-pyramid
- **Effort:** M

### BLIB-9 · P3 · Performance comparisons lack an attribution and operating path

- **Area:** bench
- **Where:** Makefile:1-26, AGENTS.md:37, scripts/mutants_gate.py:140-156
- **Problem:** There is no benchmark registry, preflight, reference comparison, run metadata, regression gate or attribution recipe; the mutation deadline and past test-suite timings are not runtime performance measurements. Scope, sample count and host validity therefore cannot be established for consumer outcomes.
- **Recommendation:** When representative workloads exist, add the bench-pyramid tiered entrypoint and preflight, pinned inputs and run metadata, interleaved reference comparison with busy-host handling, and separate low-overhead CPU sampling, allocation/RSS and I/O or syscall attribution recipes.
- **Fix with:** bench-pyramid
- **Effort:** M
