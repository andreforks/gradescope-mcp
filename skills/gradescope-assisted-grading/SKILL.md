---
name: gradescope-assisted-grading
description: Human-approved grading workflow for Gradescope assignments using the Gradescope MCP server. Use for assignment discovery, rubric review, scanned-exam grading, answer-group triage, previewing grade mutations, and only executing writes after explicit user approval.
---

# Gradescope Assisted Grading

Use this skill when grading through the Gradescope MCP server. The workflow is agent-driven, but every rubric mutation and every grade write requires explicit user approval before execution.

## Non-Negotiable Rules

- Preview first. For every write-capable tool, call it once with `confirm_write=False` before any mutation.
- Approval before execution. Only call `confirm_write=True` after the user explicitly approves that exact action.
- Read before grading. Never grade without reading the student's actual work or an answer-group sample that is clearly representative.
- Skip ambiguity. If the grade is not precise and defensible, skip and flag for human review.
- Preserve user authority. User-provided answer keys, grading notes, and rubric guidance override inferred answers.
- Prefer structured output. When a tool supports `output_format`, prefer `output_format="json"` for planning and decision-making.
- Do not confuse "leave unchanged" with "clear". In `tool_apply_grade` and `tool_grade_answer_group`, `rubric_item_ids=None` means keep current rubric state, while `rubric_item_ids=[]` means clear all rubric items.
- Default to deduction logic. Unless the question clearly indicates otherwise, assume the grading mindset is negative scoring: start from full credit, use rubric items as deductions, and treat `correct` or equivalent full-credit states as `0` deduction.
- Default to preserving existing grades. If a submission already appears graded, skip it unless the user explicitly asks for regrading, audit, or overwrite behavior.

## When To Use

Use this skill for:
- Assignment discovery and grading setup
- Handwritten or scanned PDF exams where reference answers are missing
- Batch grading via answer groups
- Individual grading with confidence gating
- Rubric drafting and rubric review before mutation

Do not use this skill for:
- Blind full automation with no user approval
- Replacing human judgment on ambiguous, subjective, or illegible work

## Workflow

### 1. Discovery

If the user does not provide IDs:
- Call `tool_list_courses`
- Call `tool_get_assignments(course_id)`
- Call `tool_get_assignment_outline(course_id, assignment_id)`
- Call `tool_get_grading_progress(course_id, assignment_id)`

Record only leaf questions with non-zero weight. Skip questions that are already fully graded unless the user explicitly asks for regrading or audit work.

For individual submissions that are already graded, skip them by default. Only re-grade previously graded submissions when:
- The user explicitly requests regrading or audit
- A rubric change was applied after the original grading
- The user confirms bulk regrading for a specific question

### 2. Build The Grading Basis

Call `tool_prepare_answer_key(course_id, assignment_id)` once per assignment and read the generated `/tmp/gradescope-answerkey-{assignment_id}.md`.

Interpret the result carefully:
- If the user directly provides reference answers, save them to a `/tmp` reference file and use that file as the primary grading reference for the rest of the run.
- If structured reference answers exist, use them.
- If reference answers are missing for scanned PDF or handwritten assignments, treat that as expected. Draft your own grading basis from the prompt, rubric, and subject knowledge.
- Your self-authored reference answer is an internal grading aid, not ground truth. If the prompt, rubric, or instructor guidance conflicts with it, defer to the prompt and rubric.

When the user provides reference answers:
- Save them to a temporary file such as `/tmp/gradescope-user-reference-{assignment_id}.md`.
- Organize them by question label or question ID if possible.
- Treat this user-supplied file as higher priority than generated fallback answers.
- If the user-provided answer conflicts with the existing rubric, stop and ask whether the rubric should be updated before grading continues.
- Treat `/tmp` as session-local cache, not durable storage. On a new conversation or restarted environment, re-read the generated answer key and ask the user to re-provide any external reference answers that are no longer present.

For each question, call `tool_prepare_grading_artifact(course_id, assignment_id, question_id)` and read `/tmp/gradescope-grading-{assignment_id}-{question_id}.md`.

Use the artifact to gather:
- Prompt text or page-reading guidance
- Rubric item IDs and descriptions
- Readiness notes
- Crop regions and relevant page URLs
- Whether the question uses positive or negative scoring

Scoring default:
- If the question metadata or rubric clearly says otherwise, follow the actual question scoring mode.
- If the question does not make the scoring mode obvious, default your grading reasoning to deduction-based scoring.
- In deduction-based scoring, select the mistakes that occurred. Do not invent positive-credit rubric logic.

Reference priority order:
1. User-provided reference answers saved in `/tmp`
2. Instructor-provided structured reference answers from Gradescope
3. Agent-drafted grading basis from prompt + rubric + subject knowledge

### 3. Rubric Review Loop

If the rubric is incomplete or unclear:
- Draft the rubric changes in chat first.
- Explain why each new or changed item is needed.
- Ask the user to approve the rubric mutation.
- State whether the question is positive-scoring or negative-scoring so the proposed weights use the correct sign.
- Default proposed rubric weights to negative values unless the question clearly uses positive scoring.

Only after approval:
- Call `tool_create_rubric_item(..., confirm_write=True)` for new items
- Call `tool_update_rubric_item(..., confirm_write=True)` for edits
- Call `tool_delete_rubric_item(..., confirm_write=True)` only when removal is clearly necessary

After any rubric mutation:
- Re-fetch the rubric with `tool_get_question_rubric`
- Present the updated rubric back to the user for confirmation that the grading basis is now correct

### 4. Choose A Grading Strategy Per Question

First check for answer groups:
- Call `tool_get_answer_groups(course_id, question_id, output_format="json")`

Prefer batch grading when:
- Answer groups exist and are readable
- The question is objective enough for group-level judgment
- Group titles, inferred answers, or crops are sufficient to justify a single grading decision for the group

Prefer individual grading when:
- No answer groups are available
- The question is subjective, proof-based, explanation-heavy, or high-risk
- Handwritten answers require per-submission reading
- The representative samples inside a group are inconsistent or not obviously equivalent

### 5. Batch Grading

For each ungraded group:
- Call `tool_get_answer_group_detail(course_id, question_id, group_id, output_format="json")`
- Read representative crops or inferred answers
- Compare the answer against your grading basis and the rubric
- Decide `rubric_item_ids` and a short justification comment
- If the detail view does not provide enough confidence that the whole group is homogeneous, do not batch grade that group
- If the existing rubric does not precisely capture the group outcome, decide whether this is a reusable rubric gap or a one-off grading exception before assigning `point_adjustment`

Preview the action:
- Call `tool_grade_answer_group(..., confirm_write=False)`

Then show the user:
- Group ID and title
- Group size
- The rubric items you intend to apply
- Your justification
- Your confidence

Only after explicit approval:
- Call `tool_grade_answer_group(..., confirm_write=True)`

If batch grading fails or the group is too ambiguous:
- Fall back to individual grading for that question

### 6. Individual Grading

Enter the question-level loop with:
- `tool_get_next_ungraded(course_id, question_id, output_format="json")`

For each submission:
- Read the grading context from `tool_get_submission_grading_context(course_id, question_id, submission_id, output_format="json")`
- If the context indicates the submission is already graded, skip it unless the user explicitly requested regrading or overwrite behavior
- Call `tool_assess_submission_readiness(course_id, assignment_id, question_id, submission_id)` before expensive reading whenever legibility, completeness, or automation suitability is uncertain
- For scanned work, call `tool_smart_read_submission(course_id, assignment_id, question_id, submission_id)` and follow the tiered read order
- If local visual review is needed, call `tool_cache_relevant_pages(course_id, assignment_id, question_id, submission_id)` and inspect the cached files in `/tmp`

Readiness-first rule:
- If readiness is clearly low, do not spend additional tokens on OCR, full-page reading, or long analysis
- Low-readiness submissions should be skipped or escalated unless the user specifically wants a manual deep read

Tiered reading order:
1. Crop region only
2. Full page if the crop is truncated or unclear
3. Adjacent pages if the reasoning spills across pages

Before grading, ask:
- Is the work legible enough?
- Do the rubric items apply unambiguously?
- Would another careful grader likely agree?

If the answer is ambiguous:
- Do not grade it
- Add it to the skipped-review list
- Move to the next submission

If the answer is gradable:
- Decide `rubric_item_ids`
- Add a concise comment
- Set an honest confidence score
- Ensure the selected rubric items match the question scoring mode:
  positive scoring means selected items add earned points
  negative scoring means selected items are deductions from full credit
- If the rubric alone cannot express the grade precisely, decide whether to use `point_adjustment` for this submission or escalate for rubric review

Preview the grade:
- Call `tool_apply_grade(..., confirm_write=False)`

Preview and debugging note:
- The write tools internally send JSON-based grade payloads. The agent does not need to construct these payloads manually, but if a preview or write behaves unexpectedly, inspect the exact rubric item IDs, point adjustment, and comment being passed rather than assuming a frontend-style form submission model.

Show the user:
- Student name and submission ID
- Selected rubric items
- Expected score impact
- Comment
- Confidence

Only after explicit approval:
- Call `tool_apply_grade(..., confirm_write=True)`

Confidence policy:
- `confidence < 0.6`: do not attempt to post; skip for human review
- `0.6 <= confidence < 0.8`: use caution and tell the user it is borderline
- `confidence >= 0.8`: acceptable for normal approval flow

### 6A. Submission-Specific Adjustments

Use `point_adjustment` when:
- The current submission has a defensible edge case that the existing rubric does not express well
- The exception is local to this submission and should not become a reusable rubric rule
- The rubric is mostly correct and only needs a narrow one-off correction

Do not use `point_adjustment` when:
- The same gap is likely to recur across many submissions
- The issue reveals that the rubric itself is incomplete or poorly structured
- You are compensating for uncertainty instead of making a precise grading judgment

Decision rule:
1. Try to grade with `rubric_item_ids` alone.
2. If that is insufficient, ask whether the gap is reusable across multiple submissions.
3. If reusable, pause and escalate to rubric review.
4. If it is clearly a one-off case, use `point_adjustment` with a specific explanation.

Adjustment discipline:
- Every `point_adjustment` must include a concise reason explaining why the rubric alone was insufficient.
- If similar adjustments appear repeatedly on the same question, stop using ad hoc adjustments and escalate for rubric review.

### 6B. Subagent Delegation Policy

Subagents may be used to reduce context pressure and parallelize grading work.

Default granularity: **one question per subagent**. Each question has its own rubric, scoring type, and reference answer, so scoping a subagent to a single question keeps context clean and scoring consistent. Merge multiple simple questions into one subagent only when they share identical rubric structure (e.g., a series of identical MCQ items).

Subagents are good for:
- OCR and page-reading work for scanned submissions
- Single-question grading loops over a pre-assigned list of submission IDs
- Independent answer-group evaluation
- Drafting grading proposals for a bounded set of submissions

#### Parallel Safety — ID Pre-Allocation

**CRITICAL:** `tool_get_next_ungraded` has race conditions under parallel use. Multiple subagents calling it simultaneously will receive stale or duplicate results, leading to "all graded" false positives and wasted turns.

**IMPORTANT:** `tool_get_assignment_submissions` returns **Global Submission IDs** — these will 404 if passed to `tool_get_submission_grading_context` or `tool_apply_grade`. You must use `tool_list_question_submissions` to get **Question Submission IDs** that work with grading tools.

Correct parallel workflow:
1. **Main agent** calls `tool_list_question_submissions(course_id, question_id, filter="ungraded")` to get Question Submission IDs.
2. **Main agent** partitions the IDs into non-overlapping batches and assigns each batch to a subagent.
3. **Subagents** grade only their assigned IDs using `tool_get_submission_grading_context(submission_id=...)` and `tool_apply_grade(submission_id=...)`. They never call `tool_get_next_ungraded`.

Prohibited in subagents:
- `tool_get_next_ungraded` — causes race conditions; only the main agent may call this
- Rubric mutations — unless the main agent explicitly delegates that exact rubric task
- Inventing scoring conventions — subagents must follow the main agent's grading contract

Main agent responsibilities:
- Define the grading basis, rubric interpretation, and scoring convention
- Pre-allocate submission IDs before spawning subagents
- Keep global consistency across submissions and questions
- Decide when a recurring issue requires rubric review
- Handle user approval flow and any final escalation decisions

Subagent prompt contract should include:
- The exact `course_id`, `assignment_id`, `question_id`, and the specific list of `submission_id`s to grade
- The question's scoring type (negative/positive) as read from the grading context
- The approved reference answer or grading basis
- The rubric item list with IDs, descriptions, and weights
- The confidence threshold for skipping
- Whether the subagent may execute writes or only preview them

When a subagent finds rubric insufficiency:
- If the issue is one-off, it may use `point_adjustment` with a specific reason
- If the issue appears reusable or likely to recur, it must stop and return the case to the main agent for rubric review
- If uncertainty is high, it must skip rather than compensate with an arbitrary adjustment

### 7. Post-Grading

After each question or grading pass:
- Call `tool_get_grading_progress(course_id, assignment_id)`

At the end:
- Call `tool_get_assignment_statistics(course_id, assignment_id)`
- Report graded counts, skipped submissions, and any low-scoring questions that may indicate rubric issues

## Safety Rules

- Never post grades without explicit user approval.
- Never mutate the rubric without explicit user approval.
- Never guess on illegible or ambiguous work.
- Always state uncertainty honestly.
- Treat missing structured reference answers on scanned PDF assignments as normal, not as an extraction failure.
- `tool_get_extensions` may be unsupported for some exam-style or scanned PDF assignments even for instructors; do not block grading on that tool.
- If the user supplies reference answers, preserve them in `/tmp` and use them consistently across all submissions in that run.
- At the start of a new conversation, do not assume prior `/tmp` reference files still exist. Rebuild them or ask the user to provide them again.
- Before grading, verify whether the question is positive-scoring or negative-scoring. Using the wrong rubric sign convention will systematically misgrade the entire question.
- For write previews, show the exact rubric item IDs, point adjustment, and comment you intend to send so the user can approve the actual mutation, not a paraphrase.
- Unless the question clearly uses positive scoring, default to deduction-based reasoning and treat full credit as zero deduction.
- Do not use submission-specific adjustments as a substitute for fixing a broken rubric that affects multiple students.
- The save-grade endpoint expects a JSON payload with `rubric_items` and `question_submission_evaluation` keys. If grading fails with HTTP 500, verify the payload format matches the expected JSON structure rather than form-encoded data.

## Minimal Tool Order

Use this default order unless the user directs otherwise:

1. `tool_get_assignment_outline`
2. `tool_get_grading_progress`
3. `tool_prepare_answer_key`
4. `tool_prepare_grading_artifact`
5. `tool_get_question_rubric`
6. Optional rubric draft and approval loop
7. `tool_get_answer_groups` to decide batch vs individual grading
8. Batch path:
   `tool_get_answer_group_detail` -> preview -> user approval -> execute
9. Individual path:
   `tool_get_next_ungraded` -> `tool_get_submission_grading_context(output_format="json")` -> `tool_assess_submission_readiness` if needed -> `tool_smart_read_submission` if needed -> `tool_cache_relevant_pages` if needed -> preview -> user approval -> execute
10. `tool_get_assignment_statistics`

## Failure Handling

- `AuthError`: stop and report immediately
- `404` on a submission: re-orient with `tool_get_next_ungraded`; the caller may have used a global submission ID
- Repeated low-confidence or skipped cases on the same question: pause that question and ask for user guidance
- If more than roughly 30% of a question's submissions are being skipped, stop auto-grading that question and escalate
- If a preview shows an unintended empty rubric state, stop. That usually means `rubric_item_ids=[]` was passed when `None` was intended.
