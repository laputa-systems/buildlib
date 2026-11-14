Prefix: BLIB
Next: 5
### BLIB-1 · P1 · Filesystem scans silently omit unreadable release inputs

- **Area:** Release archives and binary discovery
- **Where:** `src/buildlib/release.py:163`; `src/buildlib/elf.py:82-88,309-315`; `src/buildlib/macho.py:115-122,318-323`; `tests/test_release.py:193-201`
- **Problem:** Archive creation and binary discovery use Path.rglob, which suppresses directory enumeration failures. The binary predicates also convert file-read OSError into False. Proven on Python 3.15.2 with real permissions: build a valid archive containing lib/required.so, chmod source/lib to 000, then call create_archive again. It returns success, publishes only the lib directory, and replaces the complete archive. find_elfs and find_machos likewise silently omit unreadable subtrees and unreadable regular binaries. Direct reads raise PermissionError; readable controls find both binaries and recover when permissions are restored. Callers cannot tell that release inputs or verification inventories are incomplete.
- **Recommendation:** Propagate traversal, metadata, and relevant file-read failures instead of treating them as absent inputs. Preserve sorted output and the existing symlink policies. Use strict classification in find_elfs/find_machos even if their convenience predicates retain best-effort behavior. Add real permission-failure regressions for nested directories and regular binaries, with readable controls; failed archive creation must preserve a preexisting complete archive byte-for-byte and leave no temporary output.
- **Fix with:** os-boundaries
- **Effort:** M


### BLIB-2 · P2 · ELF shared libraries are misclassified as PIE executables

- **Area:** ELF inspection
- **Where:** `src/buildlib/elf.py:54-65,114-123,226-258`; `tests/test_elf.py:17-51`; `tests/binaries.py:63-133`
- **Problem:** ELFHeader.kind and pie equate ET_DYN with a PIE executable, although shared libraries use the same ELF type. Proven on Python 3.15.2: compile 'int exported(void) { return 42; }' with cc -shared -fPIC -Wl,-z,relro,-z,now,-z,noexecstack. readelf -h -d identifies a shared object with entry point zero and no PIE flag, but inspect returns kind='pie-executable', pie=True, and hardening_violations returns []. The report cannot distinguish executable artifacts from libraries, and its executable-kind claim is false. Checking RELRO/NOW/NX on a library remains useful; that is not the defect. Existing byte-built fixtures assume the same ET_DYN classification.
- **Recommendation:** Retain and interpret the metadata needed to distinguish shared libraries, dynamic PIE executables, and static PIE executables; use the supported toolchains' DF_1_PIE and interpreter metadata rather than ET_DYN alone. Correct the public report and separate any executable-kind requirement from hardening properties applicable to libraries. Add independently built or independently specified fixtures for all three kinds, including static PIE without PT_INTERP, so correcting shared-library classification cannot regress static executables.
- **Fix with:** make-invalid-states-unrepresentable
- **Effort:** M


### BLIB-3 · P2 · Malformed Mach-O normalization metadata hides file differences

- **Area:** Mach-O reproducibility
- **Where:** `src/buildlib/macho.py:172-196,288-315`; `tests/test_macho.py:93-125,142-151`
- **Problem:** Load-command parsing validates the generic eight-byte envelope, but normalization assumes every LC_UUID contains sixteen payload bytes and trusts LC_CODE_SIGNATURE ranges. Proven on Python 3.15.2 with thin little-endian arm64 byte fixtures: an LC_UUID with cmdsize=8 and no payload causes the following sixteen bytes outside that command to be zeroed. Two files with different trailers then normalize identically. A signature range at offset 1 with size 0xffffffff similarly erases almost the whole file because normalized_bytes clips the range to EOF. Malformed artifacts therefore produce false reproducibility equality instead of an error. This demonstrates a local verification defect, not an observed compromised release.
- **Recommendation:** Validate command-specific payload lengths and all normalization ranges before changing bytes. Keep UUID bytes inside their command; reject signature ranges outside the file or overlapping the header/load-command area, and enforce the signature-layout preconditions needed to classify bytes as metadata. Do not clip malformed ranges. Raise MachOError and add regressions for both witnesses, valid metadata-only differences, and differences outside metadata that must remain observable.
- **Fix with:** os-boundaries
- **Effort:** M


### BLIB-4 · P2 · Malformed mirror redirects abort fallback before a healthy mirror is tried

- **Area:** Input fetching
- **Where:** `src/buildlib/locks.py:126-137,215-219,249-255`; `tests/test_locks.py:172-233`
- **Problem:** fetch_input catches FetchError, OSError, and HTTPException, but urllib raises ValueError while parsing malformed URLs or redirect locations. Proven on Python 3.15.2 with a real local HTTPS server and normal certificate verification: a valid lock lists /bad then /good; /bad returns 302 with Location: https://[broken. fetch_input raises ValueError('Invalid IPv6 URL') after requesting only /bad. Fetching /good alone succeeds and verifies the pinned size and SHA-256. A broken primary mirror therefore defeats the advertised recovery path. load_sources also accepts the same malformed HTTPS syntax because it checks only the scheme prefix.
- **Recommendation:** Translate URL-construction and redirect-parse failures at the network boundary into FetchError with the original cause so mirror fallback continues. Validate configured URL syntax for early LockError diagnostics, but retain redirect handling because a valid URL can receive malformed remote metadata. Extend the real-origin fixture with a bad redirect followed by a healthy mirror and assert request order plus verified publication. When every mirror fails this way, require an aggregate FetchError and preservation of the previous destination.
- **Fix with:** error-model
- **Effort:** S

