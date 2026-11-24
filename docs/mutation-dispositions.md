# Mutation dispositions

The whole-change mutation gate selects changed production modules and critical modules affected by tests or configuration. It accepts the survivors below only when both the module source hash and generated mutant hash match `.mutants-gate.toml`.

To re-qualify a changed module or mutant, inspect each surviving mutation with `mutmut show`, establish its observable behavior, update its reason and hashes, and run the affected gate shard. Add tests for meaningful behavioral differences instead of accepting them.

Run the gate from the project root with a locally reachable comparison commit:

```sh
mkdir -p .work/tmp
TMPDIR="$PWD/.work/tmp" make mutants-diff BASE=<base-commit> SHARD=release
```

Run all five shards (`elf`, `macho`, `locks`, `release`, and `other`) to cover the selected union. The gate fails for unclassified paths, missing identities, invalid metadata, unsupported results, exhausted budgets, and unqualified survivors. It retries timeouts with longer limits; confirmed hangs fail.

The private ELF `_read_at` negative-range guard is unreachable through public readers: offsets and sizes come from unsigned fields or guarded nonnegative differences.

For checksum assets, undecodable POSIX names fail UTF-8 encoding regardless of ordering, although the error text can differ. Valid names have the same Unicode and UTF-8 byte order.

## elf

| Identity | Reason |
| --- | --- |
| `buildlib.elf.x__read_at__mutmut_1` | Equivalent for public callers: offsets and sizes cannot be negative. |
| `buildlib.elf.x__read_at__mutmut_6` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_at__mutmut_11` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_header__mutmut_14` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_header__mutmut_24` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_header__mutmut_27` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_header__mutmut_40` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_header__mutmut_45` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_header__mutmut_48` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_header__mutmut_55` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_header__mutmut_56` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__segment_bytes__mutmut_6` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__segment_bytes__mutmut_9` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__segment_bytes__mutmut_16` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__segment_bytes__mutmut_17` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_dynamic_entries__mutmut_12` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_dynamic_entries__mutmut_15` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_dynamic_entries__mutmut_21` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_dynamic_entries__mutmut_22` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__read_dynamic_entries__mutmut_43` | Equivalent: high-bit unknown tags are ignored by public consumers under either signedness. |
| `buildlib.elf.x__dynamic_entries__mutmut_15` | Equivalent: None and zero are both false in the following missing-table check. |
| `buildlib.elf.x__dynamic_entries__mutmut_22` | Equivalent: None and zero are both false in the following missing-table check. |
| `buildlib.elf.x__dynamic_entries__mutmut_41` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__dynamic_entries__mutmut_44` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__dynamic_entries__mutmut_51` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__dynamic_entries__mutmut_52` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__dyn_string__mutmut_5` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x__dyn_string__mutmut_24` | Equivalent: UTF-8 and utf-8 name the same Python codec. |
| `buildlib.elf.x_build_id_ranges__mutmut_13` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x_build_id_ranges__mutmut_16` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x_build_id_ranges__mutmut_22` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x_build_id_ranges__mutmut_23` | Diagnostic only: changes error text or its path or description, not rejection. |
| `buildlib.elf.x_build_id_ranges__mutmut_27` | Equivalent: with exactly 12 bytes left, no GNU name remains to produce a range. |
| `buildlib.elf.x_build_id_ranges__mutmut_28` | Equivalent: with exactly 12 bytes left, no GNU name remains to produce a range. |
| `buildlib.elf.x_hardening_violations__mutmut_40` | Diagnostic only: changes lazy-binding wording, not its condition or list entry. |
| `buildlib.elf.x_hardening_violations__mutmut_41` | Diagnostic only: changes lazy-binding wording, not its condition or list entry. |
| `buildlib.elf.x_interpreter__mutmut_15` | Equivalent: UTF-8 and utf-8 name the same Python codec. |
| `buildlib.elf.x_normalized_bytes__mutmut_6` | Equivalent: build-ID descriptors cannot start at offset zero. |
| `buildlib.elf.x_normalized_bytes__mutmut_7` | Equivalent: build-ID descriptors cannot start at offset zero. |
| `buildlib.elf.x_normalized_bytes__mutmut_8` | Equivalent: a range at EOF has zero size and empty slice assignment changes no bytes. |

## macho

| Identity | Reason |
| --- | --- |
| `buildlib.macho.x__magic_of__mutmut_8` | Diagnostic only: read error message becomes None; error and cause remain. |
| `buildlib.macho.x__load_commands__mutmut_27` | Diagnostic only: short-table error message becomes None. |
| `buildlib.macho.x__string_at__mutmut_26` | Equivalent: UTF-8 and utf-8 name the same Python codec. |
| `buildlib.macho.x__fields__mutmut_11` | Equivalent: omitted unpack offset defaults to zero. |
| `buildlib.macho.x_build_version__mutmut_5` | Diagnostic only: path is used only in _fields error text. |
| `buildlib.macho.x_build_version__mutmut_21` | Diagnostic only: path is used only in _fields error text. |
| `buildlib.macho.x_dependencies__mutmut_6` | Diagnostic only: path is used only in _fields error text. |
| `buildlib.macho.x_dylib_id__mutmut_5` | Diagnostic only: path is used only in _fields error text. |
| `buildlib.macho.x_rpaths__mutmut_6` | Diagnostic only: path is used only in _fields error text. |
| `buildlib.macho.x_signature_status__mutmut_6` | Equivalent: subprocess treats check=None and check=False as false. |
| `buildlib.macho.x_signature_metadata_ranges__mutmut_12` | Equivalent: the initially false flag is only tested for truth before being set to true. |
| `buildlib.macho.x_signature_metadata_ranges__mutmut_18` | Diagnostic only: rejection message becomes None. |
| `buildlib.macho.x_signature_metadata_ranges__mutmut_27` | Diagnostic only: rejection message becomes None. |
| `buildlib.macho.x_signature_metadata_ranges__mutmut_36` | Equivalent: omitted unpack offset defaults to zero. |
| `buildlib.macho.x_signature_metadata_ranges__mutmut_44` | Diagnostic only: rejection message becomes None. |
| `buildlib.macho.x_normalized_bytes__mutmut_6` | Equivalent: resolved ranges start after a positive-size header, including during file changes. |
| `buildlib.macho.x_normalized_bytes__mutmut_7` | Equivalent: resolved ranges start after a positive-size header, including during file changes. |
| `buildlib.macho.x_normalized_bytes__mutmut_10` | Diagnostic only: out-of-snapshot rejection message becomes None. |

## locks

| Identity | Reason |
| --- | --- |
| `buildlib.locks.x__read_object__mutmut_7` | Equivalent: UTF-8 and utf-8 name the same Python codec. |
| `buildlib.locks.x__read_object__mutmut_21` | Diagnostic only: decorates non-object rejection text; rejection and path remain. |
| `buildlib.locks.x__urls__mutmut_44` | Diagnostic only: changes missing-host detail; invalid URL rejection and cause remain. |
| `buildlib.locks.x__urls__mutmut_45` | Diagnostic only: changes missing-host detail; invalid URL rejection and cause remain. |
| `buildlib.locks.x__urls__mutmut_46` | Diagnostic only: changes missing-host detail; invalid URL rejection and cause remain. |
| `buildlib.locks.x_load_sources__mutmut_12` | Diagnostic only: decorates non-list rejection text. |
| `buildlib.locks.x_load_budgets__mutmut_12` | Diagnostic only: decorates non-object rejection text. |
| `buildlib.locks.x__download__mutmut_7` | Equivalent: HTTP header names are case-insensitive and urllib normalizes their case. |
| `buildlib.locks.x__download__mutmut_8` | Equivalent: HTTP header names are case-insensitive and urllib normalizes their case. |
| `buildlib.locks.x_fetch_input__mutmut_24` | Diagnostic only: changes the separator between retained URL failure details. |

## release

| Identity | Reason |
| --- | --- |
| `buildlib.release.x__stem__mutmut_3` | Diagnostic only: changes the version label in an error. |
| `buildlib.release.x__stem__mutmut_4` | Diagnostic only: changes the version label in an error. |
| `buildlib.release.x__ordered_object__mutmut_4` | Diagnostic only: decorates the rejected-key error text. |
| `buildlib.release.x_write_manifest__mutmut_60` | Equivalent: UTF-8 codec-name case aliases. |
| `buildlib.release.x_write_sha256sums__mutmut_20` | Equivalent: UTF-8 codec-name case aliases. |
| `buildlib.release.x_verify_sha256sums__mutmut_8` | Equivalent: UTF-8 codec-name case aliases. |
| `buildlib.release.x_verify_sha256sums__mutmut_45` | Equivalent: names decoded from UTF-8 have the same Unicode and UTF-8 byte order. |
| `buildlib.release.x_verify_sha256sums__mutmut_47` | Equivalent: names decoded from UTF-8 have the same Unicode and UTF-8 byte order. |
| `buildlib.release.x_create_archive__mutmut_38` | Equivalent: explicit None or omitted tar format selects the PAX default on supported CPython. |
| `buildlib.release.x_create_archive__mutmut_39` | Equivalent: explicit None or omitted compression preset selects default level six. |
| `buildlib.release.x_create_archive__mutmut_42` | Equivalent: explicit None or omitted tar format selects the PAX default on supported CPython. |
| `buildlib.release.x_create_archive__mutmut_43` | Equivalent: explicit None or omitted compression preset selects default level six. |
| `buildlib.release.x__checksum_assets__mutmut_3` | Diagnostic only for unsupported undecodable names; otherwise ordering is equivalent. |
| `buildlib.release.x__checksum_assets__mutmut_5` | Diagnostic only for unsupported undecodable names; otherwise ordering is equivalent. |
