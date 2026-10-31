# SYNTHETIC DEMO FIXTURE — not real class data

Every file in this folder is invented for testing and demonstration.
Student IDs (D001–D008), grader labels, scores, source files, the assignment
text and the scripted model responses are all synthetic. Nothing here describes
real students, real graders or real findings.

Known scenarios built into the fixture:

| Pair / file | Scenario |
|---|---|
| D001 vs D003 | Same rows printed with separate `print` calls vs a loop (alternate method); recorded scores differ. |
| D002 vs D004 | Same structure; D004 changes one art line. Scripted analyst summaries are identical, and the scripted reviewer contradicts them. D004 also contains a prompt-injection comment. |
| D005 | `[NAME]` placeholder inside an identifier → possible anonymization artifact, parse failure. |
| D006 | Uses a loop; deliberately outside the suggested pilot so that a note scan finds a previously unflagged submission. |
| D007 / D008 | A byte-identical helper file in both bundles plus different main files (component comparison). |

`scripted/responses.json` holds scripted mock outputs keyed by the hashes of
the files they reference. They are used only when the mock provider is
explicitly selected and are always labeled as mock output in the UI.
