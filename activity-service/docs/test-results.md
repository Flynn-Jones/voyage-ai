# activity-service Test Results

Appended by `tests/report.py` on each run — sections are never overwritten, so this file is a running pre/post-testing evidence trail across runs, per the assignment's requirement.

## Pre-testing — full activity-service suite — 2026-09-09T20:49:15

| Layer | Test | Expected | Actual | Result |
|---|---|---|---|---|
| database-service | test_health_200 | PASS | ok | ✅ PASS |
| database-service | test_list_activities_empty | PASS | ok | ✅ PASS |
| database-service | test_list_activities_returns_seeded_rows | PASS | ok | ✅ PASS |
| database-service | test_get_activity_known_id_200 | PASS | ok | ✅ PASS |
| database-service | test_get_activity_unknown_id_404 | PASS | ok | ✅ PASS |
| database-service | test_list_assignments_empty | PASS | ok | ✅ PASS |
| database-service | test_list_assignments_returns_seeded_rows | PASS | ok | ✅ PASS |
| database-service | test_get_assignment_known_id_200 | PASS | ok | ✅ PASS |
| database-service | test_get_assignment_unknown_id_404 | PASS | ok | ✅ PASS |
| database-service | test_activity_assignments_valid_activity_with_assignment | PASS | ok | ✅ PASS |
| database-service | test_activity_assignments_valid_activity_no_assignment_yet | PASS | ok | ✅ PASS |
| database-service | test_activity_assignments_unknown_activity_404 | PASS | ok | ✅ PASS |
| backend | test_add_activity_success_201 | PASS | ok | ✅ PASS |
| backend | test_add_activity_missing_required_field_400 | PASS | ok | ✅ PASS |
| backend | test_add_activity_invalid_cost_400 | PASS | ok | ✅ PASS |
| backend | test_view_activities_success | PASS | ok | ✅ PASS |
| backend | test_view_activities_database_unavailable_502 | PASS | ok | ✅ PASS |
| backend | test_view_activity_known_id_200 | PASS | ok | ✅ PASS |
| backend | test_view_activity_unknown_id_404 | PASS | ok | ✅ PASS |
| backend | test_edit_activity_success_200 | PASS | ok | ✅ PASS |
| backend | test_edit_activity_not_found_404 | PASS | ok | ✅ PASS |
| backend | test_edit_activity_invalid_cost_400 | PASS | ok | ✅ PASS |
| backend | test_delete_activity_success_200 | PASS | ok | ✅ PASS |
| backend | test_delete_activity_not_found_404 | PASS | ok | ✅ PASS |
| backend | test_ai_summary_success | PASS | ok | ✅ PASS |
| backend | test_ai_summary_missing_activity_id_400 | PASS | ok | ✅ PASS |
| backend | test_ai_summary_activity_not_found_404 | PASS | ok | ✅ PASS |
| backend | test_ai_summary_ai_service_unavailable_502 | PASS | ok | ✅ PASS |
| backend | test_ai_chat_missing_message_400 | PASS | ok | ✅ PASS |
| backend | test_ai_chat_single_match_grounds_reply_in_real_data | PASS | ok | ✅ PASS |
| backend | test_ai_chat_zero_matches_asks_for_clarification_not_invented_activity | PASS | ok | ✅ PASS |
| backend | test_ai_chat_many_matches_asks_to_narrow_instead_of_guessing | PASS | ok | ✅ PASS |
| backend | test_ai_chat_intent_extraction_unavailable_502 | PASS | ok | ✅ PASS |
| backend | test_ai_chat_activity_database_unavailable_502 | PASS | ok | ✅ PASS |
| backend | test_ai_chat_grounded_reply_generation_unavailable_502 | PASS | ok | ✅ PASS |
| backend | test_extract_intent_falls_back_on_unparseable_output | PASS | ok | ✅ PASS |
| backend | test_extract_intent_parses_real_json_shape | PASS | ok | ✅ PASS |
| backend | test_filter_candidates_respects_max_cost | PASS | ok | ✅ PASS |
| backend | test_filter_candidates_unparseable_duration_fails_open | PASS | ok | ✅ PASS |
| backend | test_filter_candidates_matches_keywords_case_insensitively | PASS | ok | ✅ PASS |
| backend | test_classify_candidates_thresholds | PASS | ok | ✅ PASS |
| backend | test_format_no_match_reply_fallback_message_differs_from_constrained | PASS | ok | ✅ PASS |
| frontend | test_health_200 | PASS | ok | ✅ PASS |
| frontend | test_hub_200 | PASS | ok | ✅ PASS |
| frontend | test_activities_list_200 | PASS | ok | ✅ PASS |
| frontend | test_activity_detail_200 | PASS | ok | ✅ PASS |
| frontend | test_activity_summary_fragment_200 | PASS | ok | ✅ PASS |
| frontend | test_register_form_200 | PASS | ok | ✅ PASS |
| frontend | test_edit_form_200 | PASS | ok | ✅ PASS |
| frontend | test_ai_mode_200 | PASS | ok | ✅ PASS |
| frontend | test_hub_degrades_gracefully_when_backend_unreachable | PASS | ok | ✅ PASS |
| frontend | test_activity_detail_404_when_activity_missing | PASS | ok | ✅ PASS |
| frontend | test_activity_summary_fragment_shows_error_not_crash | PASS | ok | ✅ PASS |

**Summary: 53/53 passed** (database-service: 12/12 passed, backend: 30/30 passed, frontend: 11/11 passed)

