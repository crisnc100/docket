Review this test-only commit from another vendor's perspective. For each changed assertion: did it get weaker, and would it now pass on broken code? Answer per assertion with PASS (not weakened) or FAIL (weakened) and one-line evidence; read the source it tests (docket/core/candidates.py, docket/core/scoring.py, docket/config or wherever thresholds are validated) as needed.

commit b89057550343e5ca810876baf0de0265facd18d3
Author: Cristian Ortega <153694889+crisnc100@users.noreply.github.com>
Date:   Mon Nov 9 16:05:20 2026 -0500

    Test-only: adjust two existing assertions for PR1 fixes
    
    - test_candidates: the non-match fixture 'UNIT-0001-extra' becomes 'UNIT-0001extra'.
      Hyphen/underscore are now valid id boundaries (f1), so the old fixture is a real
      match. The assertion still guards that an id followed by an alphanumeric
      continuation is not matched.
    - test_scoring: the conflict-override test used auto_file_threshold=0.01, which is
      now rejected at config load (f3: threshold must be >= strong_threshold). It now
      uses the minimum valid threshold. The assertion still guards that a conflict
      forces review even at the lowest allowed threshold with a maximum score.

diff --git a/tests/core/test_candidates.py b/tests/core/test_candidates.py
index 0994027..05ed0c5 100644
--- a/tests/core/test_candidates.py
+++ b/tests/core/test_candidates.py
@@ -23,7 +23,7 @@ def test_ids_in_all_sources_resolve_real_records(records, source):
     [
         "UNIT-00010",
         "XUNIT-0001",
-        "UNIT-0001-extra",
+        "UNIT-0001extra",
         "UNIT_0001",
         "UNIT-000",
         "unrelated",
diff --git a/tests/core/test_scoring.py b/tests/core/test_scoring.py
index 6cd3a82..931c5ac 100644
--- a/tests/core/test_scoring.py
+++ b/tests/core/test_scoring.py
@@ -94,8 +94,8 @@ def test_injection_id_and_gold_address_conflict_forces_review_for_either_choice(
         assert result.score == 0 and result.tier == Tier.NONE
 
 
-def test_conflict_overrides_even_maximum_score_and_minimum_threshold(config):
-    low_threshold = make_config(config, auto_file_threshold="0.01")
+def test_conflict_overrides_even_maximum_score_at_minimum_valid_threshold(config):
+    low_threshold = make_config(config, auto_file_threshold=config.strong_threshold)
     result = score_choice(
         context(
             evidence(**dict.fromkeys([s.value for s in Signal], 1.0)),
