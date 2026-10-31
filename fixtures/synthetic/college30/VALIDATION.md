# Verification record — 2026-10-31

Verified locally with the repository's Python 3.12 environment:

- 30 unique synthetic student IDs; two submissions each; 60 files with matching
  SHA-256 manifest entries.
- 59 files parse successfully. CONTROL T030 intentionally has a missing colon;
  the production import preview reports that file and its line correctly.
- All required functions exist in the 59 parseable files. Generated submission
  modules contain only function definitions and permitted imports at top level.
- 30 blank total rows and 600 blank criterion-review rows. No grades assigned.
- 1,687 reference-answer checks passed across the declared domains, including
  empty products, primes, hailstone output/counts and non-associative mergers.
- The production importer committed 30 students, 60 submissions, 60 artifacts
  and two synthetic assessments in a disposable database. A second identical
  import created no new assessments. Original-score fields stayed null.
- The production note-condition evaluator examined all 30 CONTROL submissions
  and matched the stated literal pattern in T005, T006, T007 and T025.
- The temporary database was closed and cleaned up successfully on Windows.

Reproduce with `validate.py` and `validate_import.py` as described in the README.
Only authored reference answers execute during validation. Student submissions
are parsed, hashed and imported without executing them.

These checks establish packet structure, reference behavior and importer/selector
compatibility. They do not validate every scenario description by executing the
submission, measure model accuracy, run inference, approve a real note, or test
the complete browser recording sequence. Rehearse that sequence with actual
team marks and actual completed review results before recording.

The active application database and existing private course files were not
modified. No new application routes, detectors or UI behavior were introduced.
