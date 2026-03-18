---
name: gradescope-assisted-grading
description: Human-approved grading workflow for Gradescope assignments using the Gradescope MCP server. Use for discovering assignments, drafting reference answers for scanned exams, proposing rubric changes, selecting batch vs individual grading, previewing grades, and only posting rubric changes or grades after explicit user approval.
---

# Gradescope Assisted Grading

Use this skill when grading a Gradescope assignment through the Gradescope MCP server. The workflow is agent-driven, but all rubric mutations and all grade writes require explicit human approval before execution.

## Core Policy

- Preview first. Every write-capable tool must be called with `confirm_write=False` before any mutation.
- Approval before execution. Only call `confirm_write=True` after the user explicitly approves that exact action.
- Read before grading. Never grade without reading the student's actual work.
- Skip ambiguity. If you cannot determine a precise grade, skip the submission and flag it for human review.
- User-provided reference answers override inferred ones. If the user supplies an answer key, model solution, grading notes, or a canonical answer, use that as the primary reference source.

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

Record the leaf questions with non-zero weight. Skip questions that are already fully graded.

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

For each question, call `tool_prepare_grading_artifact(course_id, assignment_id, question_id)` and read `/tmp/gradescope-grading-{assignment_id}-{question_id}.md`.

Use the artifact to gather:
- Prompt text or page-reading guidance
- Rubric item IDs and descriptions
- Readiness notes
- Crop regions and relevant page URLs

Reference priority order:
1. User-provided reference answers saved in `/tmp`
2. Instructor-provided structured reference answers from Gradescope
3. Agent-drafted grading basis from prompt + rubric + subject knowledge

### 3. Rubric Review Loop

If the rubric is incomplete or unclear:
- Draft the rubric changes in chat first.
- Explain why each new or changed item is needed.
- Ask the user to approve the rubric mutation.

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

### 5. Batch Grading

For each ungraded group:
- Call `tool_get_answer_group_detail(course_id, question_id, group_id, output_format="json")`
- Read representative crops or inferred answers
- Compare the answer against your grading basis and the rubric
- Decide `rubric_item_ids` and a short justification comment

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
- `tool_get_next_ungraded(course_id, question_id)`

For each submission:
- Read the grading context from `tool_get_submission_grading_context`
- For scanned work, also call `tool_smart_read_submission(course_id, assignment_id, question_id, submission_id)` and follow the tiered read order

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

Preview the grade:
- Call `tool_apply_grade(..., confirm_write=False)`

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
   `tool_get_next_ungraded` -> `tool_get_submission_grading_context` -> `tool_smart_read_submission` if needed -> preview -> user approval -> execute
10. `tool_get_assignment_statistics`

## Failure Handling

- `AuthError`: stop and report immediately
- `404` on a submission: re-orient with `tool_get_next_ungraded`; the caller may have used a global submission ID
- Repeated low-confidence or skipped cases on the same question: pause that question and ask for user guidance
- If more than roughly 30% of a question's submissions are being skipped, stop auto-grading that question and escalate
