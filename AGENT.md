# Gradescope MCP Server Developer Guide

## Project Objective
MCP server enabling AI assistants (Claude, Gemini, etc.) to automate Gradescope course management, grading workflows, and feedback retrieval.

## Core Technology Stack
- **Package Manager**: `uv`
- **Language**: Python 3.10+
- **Protocol**: `mcp` (FastMCP)
- **API**: `gradescopeapi` (nyuoss, v1.7.0) + reverse-engineered endpoints
- **Build**: `hatchling` (src layout)

## Authentication
- Credentials via `GRADESCOPE_EMAIL` / `GRADESCOPE_PASSWORD` env vars
- Singleton `GSConnection` in `auth.py` with auto re-login
- **Never** hardcode credentials

## MCP Tools (32 total)

### Core (All users)
1. `tool_list_courses` — List all active courses
2. `tool_get_assignments(course_id)` — Assignments and deadlines
3. `tool_get_assignment_details(course_id, assignment_id)` — Detailed assignment info
4. `tool_upload_submission(course_id, assignment_id, file_paths, confirm_write)` — Upload files

### Instructor/TA Management
5. `tool_get_course_roster(course_id)` — Full roster grouped by role
6. `tool_get_extensions(course_id, assignment_id)` — View extensions
7. `tool_set_extension(course_id, assignment_id, user_id, ..., confirm_write)` — Add/update extensions
8. `tool_modify_assignment_dates(course_id, assignment_id, ..., confirm_write)` — Change dates
9. `tool_rename_assignment(course_id, assignment_id, new_title, confirm_write)` — Rename
10. `tool_get_assignment_submissions(course_id, assignment_id)` — View all submissions
11. `tool_get_student_submission(course_id, assignment_id, student_email)` — Submission content
12. `tool_get_assignment_graders(course_id, question_id)` — View graders

### Grading — Read
13. `tool_get_assignment_outline(course_id, assignment_id)` — Question hierarchy
14. `tool_export_assignment_scores(course_id, assignment_id)` — Per-question scores CSV
15. `tool_get_grading_progress(course_id, assignment_id)` — Grading dashboard
16. `tool_get_submission_grading_context(course_id, question_id, submission_id, output_format)` — Full grading context (markdown/JSON)
17. `tool_prepare_grading_artifact(...)` — Cache prompt/rubric/reference to `/tmp`
18. `tool_assess_submission_readiness(...)` — Confidence-gated readiness
19. `tool_cache_relevant_pages(...)` — Download crop + neighbor pages to `/tmp`
20. `tool_prepare_answer_key(course_id, assignment_id)` — Assignment-wide answer key to `/tmp`
21. `tool_smart_read_submission(...)` — Tiered crop/full-page/adjacent-page reading plan

### Grading — Answer Groups (Batch)
22. `tool_get_answer_groups(course_id, question_id, output_format)` — List AI answer groups
23. `tool_get_answer_group_detail(course_id, question_id, group_id, output_format)` — Group members + crops
24. `tool_grade_answer_group(course_id, question_id, group_id, ..., confirm_write)` — Batch grade via `save_many_grades`

### Grading — Write ⚠️
25. `tool_apply_grade(course_id, question_id, submission_id, ..., confirm_write)` — Apply grade
26. `tool_create_rubric_item(course_id, question_id, description, weight, confirm_write)` — Create rubric item
27. `tool_update_rubric_item(course_id, question_id, rubric_item_id, ..., confirm_write)` — Update rubric item (cascading)
28. `tool_delete_rubric_item(course_id, question_id, rubric_item_id, confirm_write)` — Delete rubric item (cascading)
29. `tool_get_next_ungraded(course_id, question_id, submission_id)` — Navigate to next ungraded

### Regrade Requests
30. `tool_get_regrade_requests(course_id, assignment_id)` — List regrade requests
31. `tool_get_regrade_detail(course_id, question_id, submission_id)` — Regrade detail

### Statistics
32. `tool_get_assignment_statistics(course_id, assignment_id)` — Stats + alerts

## MCP Resources (3)
- `gradescope://courses` — All courses
- `gradescope://courses/{id}/assignments` — Assignments per course
- `gradescope://courses/{id}/roster` — Course roster

## MCP Prompts (7)
1. `summarize_course_progress` — Assignment status overview
2. `manage_extensions_workflow` — Guided extension management
3. `check_submission_stats` — Submission statistics
4. `generate_rubric_from_outline` — AI rubric from question structure
5. `grade_submission_with_rubric` — AI grades per question
6. `review_regrade_requests` — AI review of regrades vs rubric
7. `auto_grade_question` — Confidence-gated grading loop

## Write Safety
- All write tools default to `confirm_write=False` → returns preview, no mutation
- Must pass `confirm_write=True` to execute
- `upload_submission` requires absolute file paths

## Testing & Privacy Protocol (CRITICAL)
- Testing uses real Gradescope accounts with **sensitive student data**
- Minimize all write operations
- Log mutations in `OPERATIONS_LOGS/RECORDS.md`
- Revert all changes immediately after tests
- Never expose student names/IDs/grades in logs

## Development Standards
1. All tools handle API failures gracefully
2. Write safety via `confirm_write` + `safety.py` helpers
3. JSON output mode for agent-friendly parsing
4. Credentials from env vars only; `.env` is gitignored

## Running & Debugging
```bash
# Local execution
uv run python -m gradescope_mcp

# MCP Inspector
npx @modelcontextprotocol/inspector uv run python -m gradescope_mcp

# Tests
uv run pytest -q
```

## Development Log
See **`DEVLOG.md`** for full history: builds, test results, bugs, API discoveries.

## Known Caveats
1. `gradescopeapi` roster parsing is buggy — custom parser in `tools/courses.py`
2. `FastMCP()` v1.26 does NOT support `description` kwarg
3. Resource templates register as templates, not static resources
