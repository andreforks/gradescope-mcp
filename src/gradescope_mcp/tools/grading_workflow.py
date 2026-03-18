"""Higher-level grading workflow helpers.

These helpers make grading agents more reliable and context-efficient by:
1. Preparing a cached markdown artifact in /tmp with prompt/rubric/reference notes.
2. Recommending a read strategy that prefers crop regions before whole-page reads.
3. Producing a coarse confidence score for whether auto-grading should proceed.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any
from bs4 import BeautifulSoup

from gradescope_mcp.auth import AuthError, get_connection
from gradescope_mcp.tools.grading import _get_outline_data
from gradescope_mcp.tools.grading_ops import _get_grading_context


def _fetch_assignment_questions(course_id: str, assignment_id: str) -> dict[str, dict]:
    """Fetch question metadata for an assignment from grade.json."""
    conn = get_connection()
    url = (
        f"{conn.gradescope_base_url}/courses/{course_id}"
        f"/assignments/{assignment_id}/grade.json"
    )
    resp = conn.session.get(url)
    if resp.status_code != 200:
        raise ValueError(
            f"Cannot access grading dashboard for assignment `{assignment_id}` "
            f"(status {resp.status_code})."
        )

    data = resp.json()
    assignments = data.get("assignments", {})
    assignment = assignments.get(str(assignment_id), {})
    questions = assignment.get("questions", {})
    if not questions:
        raise ValueError(f"No questions found for assignment `{assignment_id}`.")
    return questions


def _build_question_label(question_id: str, questions: dict[str, dict]) -> str:
    """Build a human-readable question label like Q4.2 from dashboard metadata."""
    target = questions.get(str(question_id))
    if not target:
        return f"Q? ({question_id})"

    parent_id = target.get("parent_id")
    if parent_id and str(parent_id) in questions:
        parent = questions[str(parent_id)]
        return f"Q{parent.get('index', '?')}.{target.get('index', '?')}"
    return f"Q{target.get('index', '?')}"


def _find_first_submission_id(course_id: str, question_id: str) -> str:
    """Find the first available question submission id from the submissions page."""
    conn = get_connection()
    url = (
        f"{conn.gradescope_base_url}/courses/{course_id}"
        f"/questions/{question_id}/submissions"
    )
    resp = conn.session.get(url)
    if resp.status_code != 200:
        raise ValueError(
            f"Cannot access submissions page for question `{question_id}` "
            f"(status {resp.status_code})."
        )

    match = re.search(
        rf"/courses/{course_id}/questions/{question_id}/submissions/(\d+)/grade",
        resp.text,
    )
    if not match:
        raise ValueError(f"No submission found for question `{question_id}`.")
    return match.group(1)


def _extract_outline_prompt_and_reference(
    course_id: str,
    assignment_id: str,
    question_id: str,
) -> tuple[str | None, str | None]:
    """Try to extract prompt and explanation/reference from outline data."""
    try:
        props = _get_outline_data(course_id, assignment_id)
    except Exception:
        return None, None

    question = props.get("questions", {}).get(str(question_id))
    if not question:
        return None, None

    prompt_parts: list[str] = []
    explanation_parts: list[str] = []
    for item in question.get("content", []):
        item_type = item.get("type")
        value = str(item.get("value", "")).strip()
        if not value:
            continue
        if item_type == "text":
            prompt_parts.append(value)
        elif item_type == "explanation":
            explanation_parts.append(value)

    prompt = "\n\n".join(prompt_parts).strip() or None
    explanation = "\n\n".join(explanation_parts).strip() or None
    return prompt, explanation


def _extract_rubric_summary(props: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a compact rubric representation from grading props."""
    items = []
    for item in props.get("rubric_items", []):
        items.append(
            {
                "id": str(item.get("id", "")),
                "description": str(item.get("description", "")).strip(),
                "weight": item.get("weight"),
            }
        )
    return items


def _draft_reference_from_rubric(rubric_items: list[dict[str, Any]]) -> str:
    """Generate a fallback reference answer draft from rubric descriptions."""
    if not rubric_items:
        return (
            "No rubric items were available. Draft a reference answer manually "
            "before grading."
        )

    correct_items = [
        item for item in rubric_items
        if "correct" in item["description"].lower() and item["description"]
    ]
    partial_items = [
        item for item in rubric_items
        if item not in correct_items and item["description"]
    ]

    lines = [
        "This is a fallback reference-answer draft synthesized from the rubric.",
        "Use it as guidance only; verify against the scanned prompt and student work.",
    ]
    if correct_items:
        lines.append("")
        lines.append("Expected full-credit elements:")
        for item in correct_items:
            lines.append(f"- {item['description']}")

    if partial_items:
        lines.append("")
        lines.append("Common issues to watch for:")
        for item in partial_items[:8]:
            lines.append(f"- {item['description']}")

    return "\n".join(lines)


def _format_crop_regions(crop_rects: list[dict[str, Any]]) -> list[str]:
    """Format crop rectangles for human-readable markdown output."""
    lines = []
    for rect in crop_rects:
        lines.append(
            "- page {page}: x={x1}%..{x2}%, y={y1}%..{y2}%".format(
                page=rect.get("page_number", "?"),
                x1=rect.get("x1", "?"),
                x2=rect.get("x2", "?"),
                y1=rect.get("y1", "?"),
                y2=rect.get("y2", "?"),
            )
        )
    return lines


def _select_relevant_pages(
    pages: list[dict[str, Any]],
    crop_rects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only crop pages and their immediate neighbors."""
    if not pages:
        return []

    crop_page_numbers = {
        rect.get("page_number")
        for rect in crop_rects
        if rect.get("page_number") is not None
    }
    if not crop_page_numbers:
        return pages[:3]

    wanted = set()
    for page_number in crop_page_numbers:
        wanted.update({page_number - 1, page_number, page_number + 1})

    filtered = [
        page for page in pages
        if page.get("number") in wanted
    ]
    return filtered or pages[:3]


def _compute_readiness(
    prompt_text: str | None,
    reference_answer: str | None,
    crop_rects: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> tuple[float, list[str], str]:
    """Compute a readiness score: do we have enough context to START grading?

    This is NOT the same as grading confidence. Readiness checks whether we
    have the question text, reference answers, crop regions, etc. Grading
    confidence can only be determined by the AI agent AFTER reading the
    student's actual submission.

    Returns (score, reasons, action).
    """
    score = 0.25
    reasons: list[str] = []

    if prompt_text:
        score += 0.35
        reasons.append("Structured prompt text is available.")
    else:
        reasons.append("Prompt text is unavailable; grading depends on scanned pages.")

    if reference_answer:
        score += 0.2
        reasons.append("Reference answer or explanation is available.")
    else:
        reasons.append("No reference answer was found; use rubric-only grading.")

    if crop_rects:
        score += 0.1
        reasons.append("Question crop coordinates are available for targeted reading.")
    else:
        reasons.append("No crop coordinates found; must inspect whole pages.")

    if len(pages) <= 2:
        score += 0.1
        reasons.append("Few relevant pages reduce ambiguity.")
    elif len(pages) >= 5:
        reasons.append("Many pages increase the chance of cross-page spillover.")

    bounded = max(0.0, min(score, 0.95))
    if bounded >= 0.8:
        action = "ready"
    elif bounded >= 0.55:
        action = "partially_ready"
    else:
        action = "not_ready"
    return bounded, reasons, action


def prepare_grading_artifact(
    course_id: str,
    assignment_id: str,
    question_id: str,
    submission_id: str | None = None,
) -> str:
    """Prepare a cached markdown artifact in /tmp for an assignment question.

    The artifact includes question metadata, prompt text when available, rubric,
    a reference answer or fallback draft, and read-strategy notes for agents.
    """
    if not course_id or not assignment_id or not question_id:
        return "Error: course_id, assignment_id, and question_id are required."

    try:
        questions = _fetch_assignment_questions(course_id, assignment_id)
        target = questions.get(str(question_id))
        if not target:
            return (
                f"Error: question `{question_id}` was not found in assignment "
                f"`{assignment_id}`."
            )

        if submission_id is None:
            submission_id = _find_first_submission_id(course_id, question_id)

        ctx = _get_grading_context(course_id, question_id, submission_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error preparing grading artifact: {e}"

    props = ctx["props"]
    question = props.get("question", {})
    prompt_text, explanation = _extract_outline_prompt_and_reference(
        course_id, assignment_id, question_id
    )
    rubric_items = _extract_rubric_summary(props)
    reference_answer = explanation or _draft_reference_from_rubric(rubric_items)
    parameters = question.get("parameters") or {}
    crop_rects = parameters.get("crop_rect_list", [])
    pages = [
        page for page in props.get("pages", [])
        if isinstance(page, dict) and page.get("url")
    ]
    relevant_pages = _select_relevant_pages(pages, crop_rects)

    readiness, reasons, action = _compute_readiness(
        prompt_text, reference_answer, crop_rects, relevant_pages
    )
    question_label = _build_question_label(question_id, questions)

    artifact_path = pathlib.Path(
        f"/tmp/gradescope-grading-{assignment_id}-{question_id}.md"
    )

    lines = [
        f"# Grading Artifact: {question_label}",
        "",
        "## Metadata",
        f"- course_id: `{course_id}`",
        f"- assignment_id: `{assignment_id}`",
        f"- question_id: `{question_id}`",
        f"- sample_submission_id: `{submission_id}`",
        f"- weight: `{question.get('weight', target.get('weight', '?'))}`",
        f"- question_type: `{question.get('type', target.get('type', 'Unknown'))}`",
        "",
        "## Prompt",
        prompt_text or (
            "Prompt text is not available from Gradescope's structured data. "
            "Use the crop regions and page URLs below to inspect the scanned prompt."
        ),
        "",
        "## Rubric",
    ]

    if rubric_items:
        for item in rubric_items:
            lines.append(
                f"- `{item['id']}` ({item['weight']} pts): {item['description'] or '(no description)'}"
            )
    else:
        lines.append("- No rubric items found.")

    lines.extend(
        [
            "",
            "## Reference Answer",
            reference_answer or "No reference answer is available.",
            "",
            "## Read Strategy",
            "- Start with the crop region only.",
            "- If handwriting exits the crop boundary or the reasoning appears truncated, read the whole page.",
            "- If the answer still appears incomplete, inspect the previous and next page before grading.",
        ]
    )

    if crop_rects:
        lines.append("")
        lines.append("### Crop Regions")
        lines.extend(_format_crop_regions(crop_rects))

    if relevant_pages:
        lines.append("")
        lines.append("### Relevant Pages")
        for page in relevant_pages:
            lines.append(f"- page {page.get('number', '?')}: {page['url']}")

    lines.extend(
        [
            "",
            "## Readiness Assessment",
            f"- readiness: `{readiness:.2f}`",
            f"- status: `{action}`",
        ]
    )
    for reason in reasons:
        lines.append(f"- {reason}")

    lines.extend(
        [
            "",
            "## Grading Confidence (Agent Self-Report)",
            "After reading the student's answer, YOU (the agent) must assess:",
            "- **confidence**: a float 0.0-1.0 representing how sure you are about your grade",
            "- Pass this as the `confidence` parameter when calling `tool_apply_grade`",
            "- If confidence < 0.6: skip this submission and flag for human review",
            "- If confidence 0.6-0.8: grade but present to user for confirmation first",
            "- If confidence > 0.8: safe to auto-grade",
        ]
    )

    artifact_path.write_text("\n".join(lines), encoding="utf-8")
    return (
        f"Prepared grading artifact for {question_label}.\n"
        f"- Path: `{artifact_path}`\n"
        f"- Readiness: `{readiness:.2f}` ({action})\n"
        f"- **Remember:** After reading each submission, self-report your "
        f"grading confidence via the `confidence` param in `tool_apply_grade`."
    )


def assess_submission_readiness(
    course_id: str,
    assignment_id: str,
    question_id: str,
    submission_id: str,
) -> str:
    """Assess how safely an agent can auto-grade a specific submission.

    Returns the preferred read order, page/crop hints, and a confidence score
    that can be used to skip or escalate uncertain submissions.
    """
    if not course_id or not assignment_id or not question_id or not submission_id:
        return (
            "Error: course_id, assignment_id, question_id, and submission_id "
            "are required."
        )

    try:
        questions = _fetch_assignment_questions(course_id, assignment_id)
        ctx = _get_grading_context(course_id, question_id, submission_id)
        prompt_text, explanation = _extract_outline_prompt_and_reference(
            course_id, assignment_id, question_id
        )
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error assessing submission readiness: {e}"

    props = ctx["props"]
    question = props.get("question", {})
    parameters = question.get("parameters") or {}
    crop_rects = parameters.get("crop_rect_list", [])
    pages = [
        page for page in props.get("pages", [])
        if isinstance(page, dict) and page.get("url")
    ]
    relevant_pages = _select_relevant_pages(pages, crop_rects)
    page_count = len(relevant_pages)
    reference_answer = explanation or None
    readiness, reasons, action = _compute_readiness(
        prompt_text, reference_answer, crop_rects, relevant_pages
    )
    question_label = _build_question_label(question_id, questions)

    strategy = [
        "1. Read the crop region only.",
        "2. If the crop looks truncated or handwriting crosses the border, read the whole page.",
        "3. If the reasoning still looks incomplete, inspect the previous and next page.",
    ]

    # Heuristic: big crop or multi-page scanned submissions deserve caution.
    if any((rect.get("y2", 0) - rect.get("y1", 0)) > 30 for rect in crop_rects):
        reasons.append("Large crop height suggests the answer may span more than one logical block.")
        readiness = max(0.0, readiness - 0.05)
    if page_count >= 8:
        reasons.append("This submission has many scanned pages, so cross-page spillover is more likely.")
        readiness = max(0.0, readiness - 0.05)

    if readiness < 0.55:
        action = "not_ready"
    elif readiness < 0.8:
        action = "partially_ready"
    else:
        action = "ready"

    lines = [
        f"## Readiness Assessment — {question_label}",
        f"- submission_id: `{submission_id}`",
        f"- readiness: `{readiness:.2f}`",
        f"- status: `{action}`",
        "",
        "### Read Order",
    ]
    lines.extend(f"- {step}" for step in strategy)

    if crop_rects:
        lines.append("")
        lines.append("### Crop Regions")
        lines.extend(_format_crop_regions(crop_rects))

    if relevant_pages:
        lines.append("")
        lines.append("### Page URLs")
        for page in relevant_pages:
            lines.append(f"- page {page.get('number', '?')}: {page['url']}")

    lines.append("")
    lines.append("### Readiness Notes")
    for reason in reasons:
        lines.append(f"- {reason}")

    return "\n".join(lines)


def cache_relevant_pages(
    course_id: str,
    assignment_id: str,
    question_id: str,
    submission_id: str,
) -> str:
    """Download the crop page and its neighbors to /tmp for local inspection."""
    if not course_id or not assignment_id or not question_id or not submission_id:
        return (
            "Error: course_id, assignment_id, question_id, and submission_id "
            "are required."
        )

    try:
        ctx = _get_grading_context(course_id, question_id, submission_id)
        conn = get_connection()
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error caching relevant pages: {e}"

    props = ctx["props"]
    question = props.get("question", {})
    parameters = question.get("parameters") or {}
    crop_rects = parameters.get("crop_rect_list", [])
    pages = [
        page for page in props.get("pages", [])
        if isinstance(page, dict) and page.get("url")
    ]
    relevant_pages = _select_relevant_pages(pages, crop_rects)
    if not relevant_pages:
        return "No relevant pages were found for this submission."

    out_dir = pathlib.Path(
        f"/tmp/gradescope-pages-{assignment_id}-{question_id}-{submission_id}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = []
    for page in relevant_pages:
        page_number = page.get("number", "unknown")
        out_path = out_dir / f"page_{page_number}.jpg"
        response = conn.session.get(page["url"])
        response.raise_for_status()
        with open(out_path, "wb") as handle:
            handle.write(response.content)
        saved_paths.append(out_path)

    lines = [
        f"Cached {len(saved_paths)} relevant page(s) for question `{question_id}`.",
        f"- Directory: `{out_dir}`",
    ]
    for path in saved_paths:
        lines.append(f"- `{path}`")
    return "\n".join(lines)


def prepare_answer_key(course_id: str, assignment_id: str) -> str:
    """Prepare a complete answer key for an entire assignment.

    Extracts ALL questions from the assignment outline, including:
    - Question numbers, types, and weights
    - Prompt/question text (if available in structured data)
    - Explanation/reference answers (if provided by the instructor)
    - For questions without reference answers, generates a rubric-based draft

    Saves the result to /tmp/gradescope-answerkey-{assignment_id}.md.
    This file can then be referenced when grading individual submissions,
    saving context by not having to re-fetch question details each time.

    Args:
        course_id: The Gradescope course ID.
        assignment_id: The assignment ID.
    """
    if not course_id or not assignment_id:
        return "Error: course_id and assignment_id are required."

    try:
        questions = _fetch_assignment_questions(course_id, assignment_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error preparing answer key: {e}"

    # Outline data is optional (scanned exams don't have AssignmentEditor)
    try:
        outline_props = _get_outline_data(course_id, assignment_id)
    except Exception:
        outline_props = {}

    outline_questions = outline_props.get("questions", {})
    assignment_info = outline_props.get("assignment", {})
    title = assignment_info.get("title", f"Assignment {assignment_id}")

    # Build a sorted list of questions
    question_list = []
    for qid, q in questions.items():
        parent_id = q.get("parent_id")
        q_data = {
            "id": qid,
            "title": q.get("title", ""),
            "weight": q.get("weight", 0),
            "type": q.get("type", "Unknown"),
            "parent_id": parent_id,
            "index": q.get("index", 0),
        }

        # Build label
        if parent_id and str(parent_id) in questions:
            parent = questions[str(parent_id)]
            q_data["label"] = f"Q{parent.get('index', '?')}.{q.get('index', '?')}"
        else:
            q_data["label"] = f"Q{q.get('index', '?')}"

        # Extract prompt text and explanation from outline
        outline_q = outline_questions.get(str(qid), {})
        prompt_parts = []
        explanation_parts = []
        for item in outline_q.get("content", []):
            item_type = item.get("type")
            value = str(item.get("value", "")).strip()
            if not value:
                continue
            if item_type == "text":
                prompt_parts.append(value)
            elif item_type == "explanation":
                explanation_parts.append(value)

        q_data["prompt"] = "\n\n".join(prompt_parts).strip() or None
        q_data["explanation"] = "\n\n".join(explanation_parts).strip() or None

        # Only include leaf questions (with weight > 0)
        if q_data["weight"] and float(q_data["weight"]) > 0:
            question_list.append(q_data)

    # Sort by label
    question_list.sort(key=lambda x: (x.get("parent_id") or 0, x["index"]))

    # Build markdown
    lines = [
        f"# Answer Key: {title}",
        f"",
        f"- **course_id:** `{course_id}`",
        f"- **assignment_id:** `{assignment_id}`",
        f"- **Total questions:** {len(question_list)}",
        "",
    ]

    missing_answers = []

    for q in question_list:
        lines.append(f"---")
        lines.append(f"## {q['label']}: {q['title']} ({q['weight']} pts)")
        lines.append(f"- question_id: `{q['id']}`")
        lines.append(f"- type: `{q['type']}`")
        lines.append("")

        if q["prompt"]:
            lines.append("### Question")
            lines.append(q["prompt"])
            lines.append("")

        if q["explanation"]:
            lines.append("### Reference Answer")
            lines.append(q["explanation"])
            lines.append("")
        else:
            missing_answers.append(q["label"])
            lines.append("### Reference Answer")
            lines.append(
                "⚠️ No reference answer available. "
                "Please draft one or use the rubric for grading."
            )
            lines.append("")

    # Summary at the top
    if missing_answers:
        lines.insert(
            6,
            f"- **⚠️ Missing answers:** {', '.join(missing_answers)}\n",
        )

    artifact_path = pathlib.Path(f"/tmp/gradescope-answerkey-{assignment_id}.md")
    artifact_path.write_text("\n".join(lines), encoding="utf-8")

    return (
        f"✅ Answer key prepared for **{title}**\n"
        f"- Path: `{artifact_path}`\n"
        f"- Questions: {len(question_list)}\n"
        f"- Missing reference answers: {len(missing_answers)} ({', '.join(missing_answers) or 'none'})\n\n"
        f"Use this file as context when grading submissions."
    )


def smart_read_submission(
    course_id: str,
    assignment_id: str,
    question_id: str,
    submission_id: str,
) -> str:
    """Get a smart, tiered reading plan for a student's submission.

    Returns page image URLs in priority order:
    1. **Tier 1 (Crop Only):** The crop region URLs for the question's designated area.
       Agent should read ONLY this first. If the answer is fully contained, grade it.
    2. **Tier 2 (Full Page):** If handwriting exits the crop boundary or reasoning
       appears truncated, read the full page(s) containing the crop.
    3. **Tier 3 (Adjacent Pages):** If the answer still appears incomplete, read the
       previous and next pages.

    Also returns the confidence score to decide whether to auto-grade or skip.

    Args:
        course_id: The Gradescope course ID.
        assignment_id: The assignment ID.
        question_id: The question ID.
        submission_id: The question submission ID.
    """
    if not course_id or not assignment_id or not question_id or not submission_id:
        return "Error: all four IDs are required."

    try:
        questions = _fetch_assignment_questions(course_id, assignment_id)
        ctx = _get_grading_context(course_id, question_id, submission_id)
        prompt_text, explanation = _extract_outline_prompt_and_reference(
            course_id, assignment_id, question_id,
        )
    except AuthError as e:
        return f"Authentication error: {e}"
    except (ValueError, Exception) as e:
        return f"Error: {e}"

    props = ctx["props"]
    question = props.get("question", {})
    submission = props.get("submission", {})
    parameters = question.get("parameters") or {}
    crop_rects = parameters.get("crop_rect_list", [])

    pages = [
        p for p in props.get("pages", [])
        if isinstance(p, dict) and p.get("url")
    ]
    page_by_number = {p.get("number"): p for p in pages}

    question_label = _build_question_label(question_id, questions)

    # Compute readiness (pre-read context check, NOT grading confidence)
    reference = explanation or None
    readiness, reasons, action = _compute_readiness(
        prompt_text, reference, crop_rects, pages,
    )

    lines = [
        f"## Smart Read Plan — {question_label}",
        f"**Student:** {submission.get('owner_names', 'Unknown')}",
        f"**Weight:** {question.get('weight', '?')} pts",
        f"**Readiness:** `{readiness:.2f}` → `{action}`",
        "",
    ]

    # Tier 1: Crop pages only
    crop_page_numbers = sorted(set(
        r.get("page_number") for r in crop_rects if r.get("page_number") is not None
    ))

    if crop_page_numbers:
        lines.append("### Tier 1 — Crop Region (read this FIRST)")
        lines.append("Read ONLY the crop area. If the answer is fully within the box, grade it.")
        for cr in crop_rects:
            pn = cr.get("page_number", "?")
            lines.append(
                f"- Page {pn}: crop x={cr.get('x1','?')}%-{cr.get('x2','?')}%, "
                f"y={cr.get('y1','?')}%-{cr.get('y2','?')}%"
            )
        for pn in crop_page_numbers:
            p = page_by_number.get(pn)
            if p:
                lines.append(f"- 📄 Page {pn} URL: {p['url']}")
        lines.append("")

        # Tier 2: Full pages containing crop
        lines.append("### Tier 2 — Full Page (if answer overflows crop)")
        lines.append("If handwriting exits the crop box or reasoning is truncated:")
        for pn in crop_page_numbers:
            p = page_by_number.get(pn)
            if p:
                lines.append(f"- 📄 Full page {pn}: {p['url']}")
        lines.append("")

        # Tier 3: Adjacent pages
        adjacent_numbers = set()
        for pn in crop_page_numbers:
            adjacent_numbers.add(pn - 1)
            adjacent_numbers.add(pn + 1)
        adjacent_numbers -= set(crop_page_numbers)

        adjacent_pages = [
            (n, page_by_number[n]) for n in sorted(adjacent_numbers)
            if n in page_by_number
        ]

        if adjacent_pages:
            lines.append("### Tier 3 — Adjacent Pages (if answer still incomplete)")
            lines.append("Check these if student's work continues beyond the designated area:")
            for pn, p in adjacent_pages:
                lines.append(f"- 📄 Page {pn}: {p['url']}")
            lines.append("")
    else:
        # No crop regions — provide all pages
        lines.append("### No Crop Regions Available")
        lines.append("Read all available pages to find the student's answer:")
        for p in pages[:5]:
            lines.append(f"- 📄 Page {p.get('number', '?')}: {p['url']}")
        if len(pages) > 5:
            lines.append(f"- _...and {len(pages) - 5} more pages_")
        lines.append("")

    # Readiness notes
    lines.append("### Readiness Assessment")
    for reason in reasons:
        lines.append(f"- {reason}")
    lines.append("")

    if action == "not_ready":
        lines.append(
            "⚠️ **NOT READY** — Missing critical context (no prompt text, no reference). "
            "Consider skipping or requesting human assistance."
        )
    elif action == "partially_ready":
        lines.append(
            "⚡ **PARTIALLY READY** — Some context is missing. Proceed with caution."
        )
    else:
        lines.append(
            "✅ **READY** — All key context available. Good to start reading."
        )

    lines.extend(
        [
            "",
            "### Grading Confidence (Your Responsibility)",
            "After reading the student's answer, assess your own grading confidence:",
            "- **confidence 0.0-0.6**: Skip, flag for human review",
            "- **confidence 0.6-0.8**: Grade but request user confirmation",
            "- **confidence 0.8-1.0**: Safe to auto-grade",
            "- Pass your confidence score as `confidence` in `tool_apply_grade`.",
        ]
    )

    # Answer key reference
    answer_key_path = f"/tmp/gradescope-answerkey-{assignment_id}.md"
    if pathlib.Path(answer_key_path).exists():
        lines.append(f"\n📚 **Answer key available:** `{answer_key_path}`")
    else:
        lines.append(
            f"\n📚 **No answer key cached.** Run `prepare_answer_key` first for "
            f"context-efficient grading."
        )

    return "\n".join(lines)
