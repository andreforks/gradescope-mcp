# Gradescope MCP — Development Log

> This file tracks the development progress, decisions, and findings.
> Referenced by `AGENT.md` to ensure future agents have full context.

---

## Session 8 — 2026-03-18: JSON Payload Fix, Scoring Defaults, Parallel Grading Tool

### What was done

#### Bug fixes
1. **`apply_grade` / `grade_answer_group` JSON payload** (`grading_ops.py`, `answer_groups.py`):
   - Gradescope's frontend sends `Content-Type: application/json` with `{"rubric_items": {"ID": {"score": "true"}}, "question_submission_evaluation": {...}}`.
   - Old code sent form-encoded `data=` with keys like `rubric_item_ids[ID]=true`, which returned 500.
   - Fix: switched from `data=payload` to `json=payload` with the correct nested structure.

2. **`scoring_type` default was wrong** (`grading_ops.py`):
   - Default was `"positive"` (additive), but Gradescope defaults to `"negative"` (deduction: correct = 0, mistakes = negative weight).
   - Fix: changed fallback in 3 locations from `"positive"` to `"negative"`.

3. **Added scoring direction hints** (`grading_ops.py`):
   - `get_submission_grading_context` now shows: `Rubric items **add** points` (positive) or `Starts at full marks. Rubric items **deduct** points for errors.` (negative).
   - `get_question_rubric` also shows the scoring direction.
   - Prevents agents from using the wrong rubric sign convention.

#### New tool
4. **`list_question_submissions`** (`grading_ops.py`, `server.py`):
   - Scrapes all Question Submission IDs from `/questions/{qid}/submissions`.
   - Supports `filter` param: `"all"`, `"ungraded"`, `"graded"`.
   - Returns JSON with `submission_id`, `student_name`, `graded` status.
   - **Why**: `get_assignment_submissions` returns Global Submission IDs (404 with grading tools). `get_next_ungraded` has race conditions under parallel use. This tool enables the main agent to pre-allocate specific Question Submission IDs to subagents.

#### Skill updates
5. **SKILL.md** (`skills/gradescope-assisted-grading/SKILL.md`):
   - Added parallel grading best practices: one question per subagent, ID pre-allocation via `tool_list_question_submissions`.
   - Added Global ID vs Question ID distinction warning.
   - Added JSON payload debugging hint to safety rules.
   - Previously graded submission skip-by-default policy.
   - `/tmp` file persistence warning for cross-conversation sessions.

#### Docstring corrections
6. **`create_rubric_item` / `tool_create_rubric_item`** — updated weight semantics per scoring type (positive = adds points, negative = deducts points).

### New tests added
- `test_apply_grade_sends_json_payload` — verifies `json=` kwarg with correct nested structure
- `test_positive_scoring_context_shows_add_hint` — verifies positive scoring shows "add points" hint

### Test results
- **20 automated tests** — all passing
- 5 test files (unchanged)

### Files modified
| File | Changes |
|------|---------|
| `tools/grading_ops.py` | JSON payload, scoring_type default, direction hints, `list_question_submissions`, rubric weight convention fix |
| `tools/answer_groups.py` | JSON payload for `grade_answer_group` |
| `server.py` | Import + register `tool_list_question_submissions`, docstring fixes |
| `skills/.../SKILL.md` | Parallel grading policy, ID pre-allocation, batch approval, cross-agent consensus, scoring auto-detection, visual cross-validation, direct grading links, rubric weight guidance, safety rules |
| `tests/test_assignments_and_grading_ops.py` | 2 new tests |

#### Rubric weight convention fix
7. **Rubric weights are always positive** (`grading_ops.py`, `server.py`, SKILL.md):
   - Gradescope stores weights as positive numbers regardless of scoring type.
   - `scoring_type` determines interpretation: positive = earned points, negative = deducted points.
   - A deduction item with `weight=2.0` means "student loses 2 points" — the web UI shows `-2`.
   - Fixed docstrings in `create_rubric_item` and `tool_create_rubric_item` that previously told agents to pass negative values.
   - Updated scoring hints in `get_submission_grading_context` to clarify the positive-weight convention.

#### SKILL design improvements
8. **Batch approval** (SKILL.md):
   - For 50+ submissions, agents collect previews into a summary table (student, score, rubric items, confidence, link).
   - User approves in bulk: "全部通过" / "除了 #3" / "#3 改成 7 分".
   - Batch size: 10–30 per approval round.

9. **Cross-agent consensus** (SKILL.md):
   - Main agent deduplicates rubric gap reports from parallel subagents.
   - N ≥ 2 same-gap reports → one rubric proposal, not N alerts.
   - Subagent return format: `gap_description`, `affected_submission_ids`, `suggested_rubric_change`.

10. **Scoring mode auto-detection** (SKILL.md):
    - Mandatory step before grading: read `scoring_type` from grading context.
    - Stop if rubric weights conflict with stated scoring type.

11. **Visual cross-validation** (SKILL.md):
    - For numerical answers, compare crop vs full-page reading.
    - OCR disagreement → force confidence < 0.6 → flag for human review.

12. **Direct grading links** (SKILL.md):
    - Skipped submissions include clickable Gradescope link: `https://www.gradescope.com/courses/{cid}/questions/{qid}/submissions/{sid}/grade`
    - Post-grading report includes links for all skipped submissions.

### Current state
- **33 tools** + **3 resources** + **7 prompts**
- 20 automated tests (all passing)

---

## Session 7 — 2026-03-18: Bug Fix Sprint (10 fixes across 7 files)

### What was done

Systematic code review identified 12+ potential bugs; 10 were confirmed as real issues and fixed.

#### Critical fixes
1. **`get_next_ungraded` self-loop** (`grading_ops.py`):
   - Gradescope's `next_ungraded` URL points to the *current* submission when it's itself ungraded.
   - Old behavior: returned the same submission the caller was already on.
   - Fix: detects the self-loop, advances via `next_submission`, checks if the next one is ungraded, and returns it. If it's graded, follows *its* `next_ungraded`.

2. **`get_submission_grading_context` self-referencing nav** (`grading_ops.py`):
   - `previous_ungraded`/`next_ungraded` nav entries that point to the current submission are now filtered out to avoid misleading agents.

3. **`prepare_grading_artifact` fabricates "reference answer available"** (`grading_workflow.py`):
   - Rubric-drafted fallback text was passed to `_compute_readiness()` as a real reference answer, inflating the score by +0.2.
   - Fix: only real `explanation` goes into readiness scoring. The rubric draft is labeled "⚠️ Rubric-Based Fallback" in the artifact.

4. **Readiness score inconsistency** (`grading_workflow.py`):
   - Same root cause as item 3. Both `prepare_grading_artifact` and `assess_submission_readiness` now produce identical scores.

#### High-priority fixes
5. **`get_assignment_outline` missing question IDs** (`grading.py`):
   - Standalone questions (no children) now output `**Question ID:** \`{id}\`` so downstream tools can find them.

6. **Unclear 404 error for wrong submission ID type** (`grading_ops.py`):
   - 404 error now includes a contextual hint: "This often means you are using a Global Submission ID instead of a Question Submission ID."

7. **`apply_grade` / `grade_answer_group` rubric_item_ids coercion** (`grading_ops.py`, `answer_groups.py`):
   - MCP clients sometimes pass a single string `"123"` instead of `["123"]`. Both functions now auto-wrap strings into lists.

#### Medium/Low-priority fixes
8. **`get_answer_groups` markdown "Type: (not set)"** (`answer_groups.py`):
   - Falls back to per-group `question_type` when `assisted_grading_type` is None. Added Type column to the table.

9. **`get_assignment_graders` leaking internal IDs** (`submissions.py`):
   - No longer lists the filtered entries' internal IDs/labels. Only reports the count.

10. **`extensions.py` 401 for exam-type assignments** (`extensions.py`):
    - Catches 401 errors and returns a friendly message explaining that some assignment types don't support the extensions API.

11. **Reference answer UX** (`grading_workflow.py`):
    - All 3 "no reference answer" messages now explain this is expected for scanned PDF / handwritten assignments, not an extraction failure.

### Test results
- **18 automated tests** — all passing
- 5 test files: `test_write_safety.py`, `test_grading_workflow.py`, `test_answer_groups.py`, `test_assignments_and_grading_ops.py`, `test_extensions_and_answer_key.py`

### Files modified
| File | Changes |
|------|---------|
| `tools/grading_ops.py` | Self-loop fix, 404 hint, rubric_item_ids coercion |
| `tools/grading_workflow.py` | Readiness fix, reference answer labeling, UX messages |
| `tools/grading.py` | Standalone question ID output |
| `tools/answer_groups.py` | Type column, rubric_item_ids coercion |
| `tools/submissions.py` | Grader list sanitization |
| `tools/extensions.py` | 401 error handling |
| `tests/test_extensions_and_answer_key.py` | Updated assertions for new messages |

### Current state
- **32 tools** + **3 resources** + **7 prompts**
- 18 automated tests (all passing)

---

## Session 6 — 2026-03-17: Answer Groups, Rubric CRUD, JSON Output

### What was done
1. **Answer Groups** — 3 new tools in `tools/answer_groups.py`:
   - `get_answer_groups(course_id, question_id)` → lists all AI-clustered answer groups with sizes
   - `get_answer_group_detail(course_id, question_id, group_id)` → shows members, crops, graded status
   - `grade_answer_group(course_id, question_id, group_id, ...)` → batch-grades via `save_many_grades`
   - Both markdown and JSON output supported

2. **Rubric CRUD** — 2 new tools in `tools/grading_ops.py`:
   - `update_rubric_item(...)` → modify description/weight (cascades to all submissions)
   - `delete_rubric_item(...)` → remove item (cascades to all submissions)
   - Both have `confirm_write` gates with cascade warnings

3. **JSON Output Mode**:
   - `get_submission_grading_context` now accepts `output_format="json"` for structured data
   - Returns parsed rubric items, navigation, answer group, progress, pages, crops
   - `get_answer_groups` and `get_answer_group_detail` also support JSON mode

### Key API discoveries
- `/courses/{cid}/questions/{qid}/answer_groups` → full JSON with all groups + submissions
- Group grading uses `save_many_grades` endpoint (not `save_grade`)
- Rubric items support PUT (update) and DELETE on `/rubric_items/{item_id}`
- SubmissionGrader props contain answer group metadata: `answer_group`, `answer_group_size`, `groups_present`

### Test results
| Tool | Test Data | Result |
|------|-----------|--------|
| `get_answer_groups` (markdown) | Q5a (midterm) | ✅ 20 groups × 164 submissions |
| `get_answer_groups` (JSON) | Q5a | ✅ Structured JSON with group sizes |
| `get_submission_grading_context` (JSON) | Q5a sub | ✅ rubric_items, navigation, answer_group parsed |
| `update_rubric_item` (dry run) | Q5a | ✅ Cascade warning shown |
| `delete_rubric_item` (dry run) | Q5a | ✅ Cascade warning shown |
| `grade_answer_group` (dry run) | mocked | ✅ Confirmation gate with group_size |

### Current state
- **32 tools** + **3 resources** + **7 prompts**
- 10 automated tests (all passing)

---

## Session 5 — 2026-03-17: Safety Rails, Tests, and Agent Hardening

### What was done
1. Added a two-step confirmation gate for write-capable tools:
   - `tool_upload_submission(..., confirm_write=False)`
   - `tool_set_extension(..., confirm_write=False)`
   - `tool_modify_assignment_dates(..., confirm_write=False)`
   - `tool_rename_assignment(..., confirm_write=False)`
   - `tool_apply_grade(..., confirm_write=False)`
   - `tool_create_rubric_item(..., confirm_write=False)`
2. Fixed upload path validation:
   - uploads now require **absolute** paths
   - removed the ineffective post-`resolve()` `..` traversal check
3. Hardened scanned-page caching:
   - `cache_relevant_pages()` now downloads page images through the authenticated
     Gradescope session instead of `urllib.request.urlopen`
4. Added the first automated test suite:
   - write confirmation tests
   - upload path validation test
   - cached page download/authenticated session test
5. Synced project docs:
   - `README.md` and `AGENT.md` now reflect **27 tools** and **7 prompts**
   - documented the `confirm_write=True` requirement

### Why this matters for agents
- A default-deny write path is a better fit for MCP clients, where LLMs may call
  tools speculatively or before the user has explicitly approved a mutation.
- The server now behaves more like a two-step workflow:
  1. preview intended mutation
  2. execute only with `confirm_write=True`
- Authenticated page downloads are more reliable for sandboxed/short-lived
  signed URLs and avoid a separate unauthenticated HTTP stack.

### Remaining gaps
- No structured JSON mode yet for high-volume read tools such as roster/scores.
- Live read-only validation still depends on the local `.env` credentials and
  network availability in the execution environment.

## Session 1 — 2026-03-17: Initial Implementation

### What was done
1. **Project initialization**: `uv init`, added deps (`mcp`, `gradescopeapi`, `python-dotenv`), configured `hatchling` src layout in `pyproject.toml`.
2. **Implemented 12 MCP tools**, 3 resources, 3 prompts:
   - See `AGENT.md` for full tool list.
3. **Live-tested** against real Gradescope account (UCI, instructor for STATS 67 etc.).

### Test results
| Tool | Status | Notes |
|------|--------|-------|
| `list_courses` | ✅ | 9 instructor + 5 student courses returned |
| `get_assignments` | ✅ | 15 assignments for STATS 67 W26 (course `1205064`) |
| `get_course_roster` | ✅ | 170 members parsed correctly |
| `get_extensions` | ✅ | Returns empty correctly when no extensions exist |
| Others | 🔲 | Not yet live-tested (need specific scenarios) |

### Bug found & fixed: `gradescopeapi` roster parsing
- **Symptom**: `get_course_users()` silently returns `None`.
- **Root cause**: In `_course_helpers.py:172`, the library hardcodes `num_submissions_column = 4 if has_sections else 3`, but the actual Gradescope table now has 9 columns (added Canvas column, dual name columns, etc.). It tries `int("stats-67-lec-b-...")` and crashes. The `account.py:get_course_users` catches all exceptions with bare `except Exception: return None`.
- **Fix**: Wrote a custom parser in `tools/courses.py` (`_parse_roster()`) that:
  - Detects the "Submissions" column by matching header text.
  - Parses `data-sections` as JSON (now a JSON array, not a plain string).
  - Extracts `user_id` from `js-rosterName` button's `data-url`.
  - Includes `user_id` in output so TAs can use `set_extension` easily.

### Key architecture decisions
- **`FastMCP`** (not raw `Server`): Simpler decorator-based tool registration.
- **Singleton `GSConnection`** in `auth.py`: Avoids re-login per tool call.
- **Custom roster parser**: Bypasses buggy `gradescopeapi` implementation.
- **`FastMCP()` constructor**: v1.26 does NOT support `description` kwarg — removed it.
- **`pyproject.toml`**: Uses `hatchling` build system with `[tool.hatch.build.targets.wheel] packages = ["src/gradescope_mcp"]` for src layout.
- **`dependency-groups`**: `[tool.uv.dev-dependencies]` is deprecated → use `[dependency-groups]`.

### Known limitations
- `gradescopeapi` is web-scraping based — if Gradescope changes its HTML, tools may break.
- `get_assignment_submissions` is slow for large classes (1 HTTP request per submission).
- `remove_student_extension` is `NotImplementedError` upstream.
- Image/PDF-only submissions are not supported by gradescopeapi.
- Resource templates (`gradescope://courses/{id}/...`) register as templates, not static resources.

---

## Pending / Future Work
- [ ] Test `upload_submission`, `set_extension`, `modify_assignment_dates` with real data
- [ ] Test `get_assignment_submissions` and `get_student_submission` (instructor-only)
- [ ] Add error retry logic for network failures
- [ ] Consider session expiration handling (auto re-login)
- [ ] Phase 5c: Outline save endpoint (experimental write operation)
- [ ] Publish to PyPI

---

## Session 2 — 2026-03-17: Grading Tools + AI Rubric Prompts

### What was done
1. **Added 4 instructor tools** (Phase 4 expansion): `rename_assignment`, `get_assignment_submissions`, `get_student_submission`, `get_assignment_graders` → 12 total tools.
2. **Reverse-engineered Gradescope grading endpoints**:
   - `/outline/edit` → `AssignmentEditor` React component with full question hierarchy (IDs, types, weights, content text, answer keys)
   - `/scores` → CSV export of per-question scores + student metadata
   - `/export_evaluations` → ZIP of evaluation data
   - `/grade.json` → Dashboard JSON with question links, grader assignments, graded counts
   - `/questions/{qid}/submissions` → Per-question grading SPA (client-side rendered, no server-side React)
   - The grading SPA is loaded via `common-*.js` webpack bundle (184 rubric-related code snippets found)
   - Data model: `RubricItem` + `RubricItemGroup`, linked via annotations
3. **Implemented Phase 5a — 3 grading tools** in `tools/grading.py`:
   - `get_assignment_outline` — parses `AssignmentEditor` props for question tree
   - `export_assignment_scores` — fetches `/scores` CSV, computes stats
   - `get_grading_progress` — parses `/grade.json` for per-question progress
4. **Implemented Phase 5b — 2 AI prompts**:
   - `generate_rubric_from_outline` — AI generates rubric from question structure
   - `grade_submission_with_rubric` — AI grades a student's submission

### Test results
| Tool | Status | Notes |
|------|--------|-------|
| `get_assignment_outline` | ✅ | 22 questions in 4 groups, question text + answer keys extracted |
| `export_assignment_scores` | ✅ | 166 students, 157 graded, avg 16.63/18, min 2.0, max 18.0 |
| `get_grading_progress` | ✅ | 100% graded (2826/2826), shows grader assignments (John Henry Lain) |
| `generate_rubric_from_outline` | ✅ registered | Prompt-based, needs end-to-end testing in AI client |
| `grade_submission_with_rubric` | ✅ registered | Prompt-based, needs end-to-end testing in AI client |

### Reverse engineering notes
- **No public rubric API**: Gradescope has no `rubric_items.json` or similar endpoint. The rubric data is loaded client-side by the React SPA via XHR calls made from the `common-*.js` webpack bundle.
- **Endpoint probing results** (using `assignment_id=7743243`):
  - `GET /scores` → 200, text/csv (per-question grades)
  - `GET /export_evaluations` → 200, application/zip
  - `GET /grade.json` → 200, JSON (dashboard data)
  - `GET /outline/edit` → 200, HTML with `AssignmentEditor` React props
  - `POST /outline` → saveUrl for assignment editor (untested, risky)
  - `GET /rubric`, `/rubric_items`, `/scores.json` → 404
- **JS bundle analysis**: The `common-*.js` bundle contains `RubricItem`, `RubricItemGroup`, `QuickMarkTemplate` types with annotation-based grading. Rubric items are linked to submissions via `annotationLinkType`.

### Current state
- **15 tools** + **1 resource** + **2 resource templates** + **5 prompts**
- All tools live-tested (except write operations and AI prompts)

---

## Session 2 (continued) — Submission Content Extraction

### What was done
1. **Implemented `get_student_submission_content()`** — unified function to extract submission content:
   - **Online assignments**: Parses `AssignmentSubmissionViewer` React component props for text answers + AWS S3 image URLs
   - **Scanned PDF exams**: Extracts per-page JPG images + full PDF URL via regex on embedded JSON in raw HTML
2. **Replaced buggy `gradescopeapi.get_assignment_submission`** — the upstream function had an `UnboundLocalError` for `aws_links` and silently failed on PDF/image submissions.
3. **Rewired `tool_get_student_submission`** in `submissions.py` to use the new custom implementation.

### Test results
| Format | Assignment | Status | Details |
|--------|-----------|--------|---------|
| Online | hw9-regression (W26) | ✅ | 18 question answers extracted, 4 image URLs (PNG screenshots) |
| Scanned PDF | midterm (F25) | ✅ | 16 page JPGs (2332×3297) + full PDF URL extracted |
| Online (regression) | Same student as before | ✅ | Backward compatible, same output as before |

### Key findings
- **Online assignments**: Use `AssignmentSubmissionViewer` React component with `question_submissions` array and `text_files` array
- **Scanned PDF exams**: No React components at all (0 found). Page data embedded as JSON in single-line HTML. Pattern: `"number":N,"width":W,"height":H,"url":"...page_N.jpg..."`
- **PDF attachment**: Scanned exams also include full PDF at `"url":"...output.pdf..."` with `page_count` metadata

---

## Session 3 — Regrade Requests & Statistics

### Phase 6: Regrade Requests ✅
- **New module:** `tools/regrades.py`
- **`get_regrade_requests`**: Parses `/assignments/{id}/regrade_requests` HTML table → student, question, grader, status, question_id, submission_id
- **`get_regrade_detail`**: Parses `SubmissionGrader` React component from `/questions/{qid}/submissions/{sid}/grade` → student_comment, staff_comment, rubric items, evaluations
- **`review_regrade_requests`** prompt: AI reviews all pending requests vs rubric

### Phase 7: Assignment Statistics ✅
- **New module:** `tools/statistics.py`
- **`get_assignment_statistics`**: Fetches `/statistics.json` → assignment-level summary (mean/median/min/max/std) + per-question breakdowns + low-scoring question alerts

### Test results
| Tool | Test Data | Result |
|------|-----------|--------|
| `get_regrade_requests` | STATS 67 F25 midterm | ✅ 10 pending requests listed |
| `get_regrade_detail` | Q5a regrade (student argued correct labeling) | ✅ Student + staff comments extracted |
| `get_assignment_statistics` | Same midterm | ✅ Mean 87.3%, Q2a flagged at 64% |

### Current state
- **22 tools** + **3 resources** + **6 prompts**
- All tools live-tested

### Phase 8: Grading Write Operations ✅
- **New module:** `tools/grading_ops.py`
- `get_submission_grading_context`: Rubric items (IDs), score, point adjustment, comments, navigation URLs, scanned pages
- `apply_grade`: POST to `save_grade` with CSRF → apply rubric items + point adjustment + comment
- `create_rubric_item`: POST to `rubric_items` → create new rubric items
- `get_next_ungraded`: Navigate via `next_ungraded` URL

#### Key endpoints discovered
- `urls` dict: 26 endpoints (`save_grade`, `rubric_item`, `save_comment_url`, `update_rubric_entries`)
- `evaluation`: `points` (adjustment), `comments`, `question_submission_id`
- `navigation_urls`: 18 links (`previous_ungraded`, `next_ungraded`, `next_alphabetical`, etc.)
- CSRF: `<meta name="csrf-token">` tag

---

## Session 4 — 2026-03-17: Agent-Oriented Grading Workflow

### What was done
1. **Fixed a real grading context bug** in `tools/grading_ops.py`:
   - `question.parameters` can be `None` on real grading pages.
   - `get_submission_grading_context()` previously crashed with `AttributeError`.
   - Added a null-safe fallback before reading `crop_rect_list`.
2. **Added `tools/grading_workflow.py`** for agent-friendly grading preparation:
   - `prepare_grading_artifact(course_id, assignment_id, question_id, submission_id=None)`
     writes `/tmp/gradescope-grading-{assignment_id}-{question_id}.md`
   - `assess_submission_readiness(course_id, assignment_id, question_id, submission_id)`
     returns a crop-first read plan and confidence gate
   - `cache_relevant_pages(course_id, assignment_id, question_id, submission_id)`
     downloads only the crop page and adjacent pages to `/tmp`
3. **Registered 3 new MCP tools** in `server.py`:
   - `tool_prepare_grading_artifact`
   - `tool_assess_submission_readiness`
   - `tool_cache_relevant_pages`
4. **Updated packaging/docs**:
   - Added explicit `beautifulsoup4` dependency to `pyproject.toml`
   - Updated `README.md` tool count and grading workflow docs

### Workflow design decisions
- **OCR stays in the agent, not the MCP server**:
  - The server should provide crop coords, page images, rubric, reference notes,
    and confidence gates.
  - The agent should perform visual reading / OCR / answer interpretation.
- **Crop-first read order**:
  1. Read crop region only
  2. If answer looks truncated or exits the box, read the whole page
  3. If still incomplete, inspect previous and next page
- **Context minimization**:
  - Artifact/readiness outputs now keep only the crop page and immediate neighbors
    instead of dumping all submission pages.
- **Confidence gate**:
  - `auto_grade_ok`
  - `review_before_write`
  - `skip_or_human_review`

### Real-world tests performed
#### Test A — Real grading page read
- URL tested:
  `/courses/1205064/questions/67345805/submissions/3753176143/grade`
- Result:
  - Successfully fetched rubric + score state
  - Found this was an **ungraded** 5-point question
  - Confirmed write-path testing should not proceed without explicit grading intent

#### Test B — Real scanned final exam prompt extraction
- Assignment:
  `7843953`
- Located `Question 4` from `grade.json`:
  - `Q4.1` → `67943014`
  - `Q4.2` → `67943015`
  - `Q4.3` → `67943016`
  - `Q4.4` → `67943017`
- Downloaded and inspected page 4/page 5 images
- Successfully reconstructed the prompt text for Question 4 from scanned pages

#### Test C — Real workflow generation for Q4.3
- `prepare_grading_artifact('1205064', '7843953', '67943016')`
  → created:
  `/tmp/gradescope-grading-7843953-67943016.md`
- `assess_submission_readiness('1205064', '7843953', '67943016', '3775836425')`
  → returned a low-confidence recommendation:
  `skip_or_human_review`
- `cache_relevant_pages('1205064', '7843953', '67943016', '3775836425')`
  → downloaded:
  - `/tmp/gradescope-pages-7843953-67943016-3775836425/page_4.jpg`
  - `/tmp/gradescope-pages-7843953-67943016-3775836425/page_5.jpg`
  - `/tmp/gradescope-pages-7843953-67943016-3775836425/page_6.jpg`

### Current state
- **27 tools** + **3 resources** + **7 prompts**
- Read-side grading workflow is now much closer to agent use:
  - prompt/rubric/reference artifact in `/tmp`
  - crop-first read strategy
  - adjacent-page fallback guidance
  - confidence-based skip/escalate recommendation

### Session 4 continuation — Smart Grading Pipeline

#### New tools added
- **`prepare_answer_key(course_id, assignment_id)`**: Extracts ALL questions from outline + grade.json → produces `/tmp/gradescope-answerkey-{assignment_id}.md`. Gracefully handles scanned exams (no AssignmentEditor). Identifies missing reference answers.
- **`smart_read_submission(course_id, assignment_id, question_id, submission_id)`**: Returns 3-tier reading plan:
  - Tier 1: Crop region only (with coordinates)
  - Tier 2: Full page (if answer overflows crop)
  - Tier 3: Adjacent pages (if still incomplete)
  - Includes confidence score + recommended action
  - Notes whether answer key is cached

#### New prompt
- **`auto_grade_question`**: Full workflow prompt: prepare answer key → prepare artifact → for each submission: smart read → assess confidence → apply grade → navigate next

#### Test results
| Tool | Test Data | Result |
|------|-----------|--------|
| `prepare_answer_key` | STATS 67 midterm (scanned) | ✅ 31 questions extracted, all missing answers noted |
| `smart_read_submission` | Q5a submission | ✅ Tier 1: page 9 crop (17-28% × 15-40%), Tier 2: full page 9, Tier 3: pages 8+10. Confidence 0.35 → skip |

### Remaining gaps / next steps
- [ ] Add a structured `plan_grade_submission` tool that prepares a write-free grading proposal
- [ ] Improve scanned-question prompt extraction so the artifact can store prompt text directly when page images are available
- [ ] Replace heuristic confidence with stronger signals from actual answer coverage / cross-page detection
