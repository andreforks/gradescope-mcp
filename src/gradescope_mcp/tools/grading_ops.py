"""Grading write operation tools.

These tools enable AI agents to actually grade submissions on Gradescope:
apply rubric items, set point adjustments, add comments, create rubric items,
and navigate between submissions.

All write operations require CSRF tokens extracted from the grading page.
"""

import json
import logging
import re

from bs4 import BeautifulSoup

from gradescope_mcp.auth import get_connection, AuthError
from gradescope_mcp.tools.safety import write_confirmation_required

logger = logging.getLogger(__name__)

_MISSING_PDF_MARKER = "missing_pdf"


def _normalize_url(url: str) -> str:
    """Normalize protocol-relative URLs to https."""
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _is_placeholder_page(page: dict) -> bool:
    """Check if a page is a placeholder/missing PDF image."""
    url = page.get("url", "")
    return _MISSING_PDF_MARKER in url or not url


def _get_grading_context(course_id: str, question_id: str, submission_id: str) -> dict:
    """Fetch the SubmissionGrader page and extract all context needed for grading.

    Returns a dict with:
        - props: the SubmissionGrader React component props
        - csrf_token: the CSRF token for POST requests
        - session: the authenticated session
        - base_url: Gradescope base URL
    """
    conn = get_connection()
    url = (
        f"{conn.gradescope_base_url}/courses/{course_id}"
        f"/questions/{question_id}/submissions/{submission_id}/grade"
    )
    resp = conn.session.get(url)

    if resp.status_code != 200:
        hint = ""
        if resp.status_code == 404:
            hint = (
                " This often means you are using a Global Submission ID "
                "(from get_assignment_submissions) instead of a Question "
                "Submission ID. Use get_next_ungraded or get_grading_progress "
                "to obtain the correct Question Submission ID."
            )
        raise ValueError(
            f"Cannot access grading page (status {resp.status_code}).{hint}"
        )

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract CSRF token
    csrf_meta = soup.find("meta", {"name": "csrf-token"})
    csrf_token = csrf_meta.get("content", "") if csrf_meta else ""

    # Extract SubmissionGrader props
    grader = soup.find(attrs={"data-react-class": "SubmissionGrader"})
    if not grader:
        raise ValueError("SubmissionGrader component not found.")

    props = json.loads(grader.get("data-react-props", "{}"))

    return {
        "props": props,
        "csrf_token": csrf_token,
        "session": conn.session,
        "base_url": conn.gradescope_base_url,
    }


def get_submission_grading_context(
    course_id: str,
    question_id: str,
    submission_id: str,
    output_format: str = "markdown",
) -> str:
    """Get the full grading context for a specific question submission.

    Returns the current rubric items, applied evaluations, score, comments,
    navigation URLs, and student info. This is what you need before grading.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        submission_id: The question submission ID (NOT the assignment submission ID).
        output_format: "markdown" (default) or "json" for structured output.
    """
    if not course_id or not question_id or not submission_id:
        return "Error: course_id, question_id, and submission_id are required."

    try:
        ctx = _get_grading_context(course_id, question_id, submission_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching grading context: {e}"

    props = ctx["props"]
    question = props.get("question", {})
    submission = props.get("submission", {})
    evaluation = props.get("evaluation", {})
    nav = props.get("navigation_urls", {})

    # Rubric items + evaluations
    rubric_items = props.get("rubric_items", [])
    evaluations = props.get("rubric_item_evaluations", [])
    applied_ids = {e["rubric_item_id"] for e in evaluations if e.get("present")}

    # Navigation parsing — filter out self-referencing ungraded links
    # (Gradescope sets next_ungraded/previous_ungraded to the current
    # submission when it is itself ungraded, which misleads agents)
    nav_parsed = {}
    for label, key in [
        ("previous_ungraded", "previous_ungraded"),
        ("next_ungraded", "next_ungraded"),
        ("previous_submission", "previous_submission"),
        ("next_submission", "next_submission"),
        ("previous_question", "previous_question"),
        ("next_question", "next_question"),
    ]:
        url = nav.get(key, "")
        if url:
            qid_m = re.search(r"/questions/(\d+)", url)
            sid_m = re.search(r"/submissions/(\d+)", url)
            if qid_m and sid_m:
                parsed_sid = sid_m.group(1)
                # Skip self-referencing ungraded links
                if key in ("previous_ungraded", "next_ungraded") and parsed_sid == submission_id:
                    continue
                nav_parsed[label] = {
                    "question_id": qid_m.group(1),
                    "submission_id": parsed_sid,
                }

    # Answer group info
    answer_group_id = props.get("answer_group")
    answer_group_size = props.get("answer_group_size")
    groups_present = props.get("groups_present", False)

    # Extract text answer (for online assignments)
    answers_data = submission.get("answers", {})
    text_content = []
    if isinstance(answers_data, dict):
        for key, val in answers_data.items():
            if isinstance(val, str):
                text_content.append(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        text_content.append(item)
                    elif isinstance(item, dict) and "text_file_id" in item:
                        text_content.append(f"[Uploaded file ID: {item['text_file_id']}]")
                    else:
                        text_content.append(str(item))
    text_answer = "\n".join(text_content).strip() if text_content else None

    # Pages
    pages = props.get("pages", [])
    parameters = question.get("parameters") or {}
    crop = parameters.get("crop_rect_list", [])

    if output_format == "json":
        result = {
            "question_id": question_id,
            "submission_id": submission_id,
            "question_title": question.get("title", ""),
            "weight": question.get("weight"),
            "scoring_type": question.get("scoring_type", "negative"),
            "student": submission.get("owner_names", "Unknown"),
            "score": submission.get("score"),
            "graded": submission.get("graded", False),
            "point_adjustment": evaluation.get("points"),
            "comments": evaluation.get("comments", ""),
            "text_answer": text_answer,
            "rubric_items": [
                {
                    "id": str(ri["id"]),
                    "description": ri.get("description", ""),
                    "weight": ri.get("weight"),
                    "applied": ri["id"] in applied_ids,
                    "position": ri.get("position"),
                    "locked": ri.get("locked", False),
                }
                for ri in rubric_items
            ],
            "navigation": nav_parsed,
            "progress": {
                "graded": props.get("num_graded_submissions", 0),
                "total": props.get("num_submissions", 0),
            },
            "answer_group": {
                "id": str(answer_group_id) if answer_group_id else None,
                "size": answer_group_size,
                "groups_present": groups_present,
            } if groups_present else None,

            "pages": [
                {"number": p.get("number"), "url": _normalize_url(p.get("url", ""))}
                for p in pages if isinstance(p, dict) and p.get("url")
                and not _is_placeholder_page(p)
            ][:5],
            "crop_regions": crop,
        }
        return json.dumps(result, indent=2)

    # Markdown output
    lines = [f"## Grading Context — Q{question.get('title', '?')}"]
    lines.append(f"**Question ID:** `{question_id}` | **Question Submission ID:** `{submission_id}`")
    lines.append(f"**Student:** {submission.get('owner_names', 'Unknown')}")
    lines.append(f"**Weight:** {question.get('weight', '?')} pts")
    lines.append(f"**Current Score:** {submission.get('score', 'Ungraded')}")
    lines.append(f"**Graded:** {submission.get('graded', False)}")

    # Scoring type
    scoring_type = question.get("scoring_type", "negative")
    lines.append(f"**Scoring:** {scoring_type} (floor={question.get('floor')}, ceiling={question.get('ceiling')})")
    if scoring_type == "positive":
        lines.append("  ↳ _Rubric items **add** points. Weight values are positive (e.g., `5.0` = +5 earned)._")
    else:
        lines.append("  ↳ _Starts at full marks. Rubric items **deduct** points. Weight values are positive (e.g., `2.0` = −2 deducted). Gradescope handles the sign internally._")

    # Current evaluation (comments + point adjustment)
    if evaluation:
        points = evaluation.get("points")
        comments = evaluation.get("comments", "")
        lines.append(f"\n**Point Adjustment:** {points if points is not None else 'None'}")
        if comments:
            lines.append(f"**Comments:** {comments}")

    # Answer group
    if groups_present and answer_group_id:
        lines.append(f"\n**Answer Group:** `{answer_group_id}` ({answer_group_size} in group)")

    if text_answer:
        lines.append(f"\n### Student Answer")
        lines.append(f"{text_answer}")

    if rubric_items:
        lines.append(f"\n### Rubric Items ({len(rubric_items)})")
        lines.append("| Applied | ID | Description | Points |")
        lines.append("|---------|-----|-------------|--------|")
        for ri in rubric_items:
            applied = "✅" if ri["id"] in applied_ids else "—"
            lines.append(
                f"| {applied} | `{ri['id']}` | {ri['description']} | {ri['weight']} |"
            )

    # Navigation
    lines.append(f"\n### Navigation")
    for label, ids in nav_parsed.items():
        lines.append(f"- **{label}**: qid=`{ids['question_id']}`, sid=`{ids['submission_id']}`")

    lines.append(f"\n**Progress:** {props.get('num_graded_submissions', 0)}/{props.get('num_submissions', 0)} graded")

    # Scanned PDF pages for this question
    if pages:
        lines.append(f"\n### Submission Pages ({len(pages)})")
        if crop:
            crop_pages = sorted(set(c.get("page_number") for c in crop if "page_number" in c))
            lines.append(f"**Relevant pages:** {crop_pages}")
        for p in pages[:3]:
            if isinstance(p, dict) and p.get("url") and not _is_placeholder_page(p):
                page_num = p.get('number') or '?'
                lines.append(f"- Page {page_num}: [View]({_normalize_url(p['url'])})")
        real_pages = [p for p in pages if isinstance(p, dict) and not _is_placeholder_page(p)]
        if len(real_pages) > 3:
            lines.append(f"- _...and {len(real_pages) - 3} more pages_")

    return "\n".join(lines)


def get_question_rubric(course_id: str, question_id: str) -> str:
    """Get rubric items for a question without requiring a submission ID.

    Useful when you know the question_id from get_assignment_outline but
    don't have a specific submission ID. Auto-discovers a submission to
    extract rubric data.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID from outline.
    """
    if not course_id or not question_id:
        return "Error: both course_id and question_id are required."

    try:
        conn = get_connection()
        ctx = None

        # Path 1: submissions page
        url1 = (
            f"{conn.gradescope_base_url}/courses/{course_id}"
            f"/questions/{question_id}/submissions"
        )
        resp = conn.session.get(url1)
        if resp.status_code == 200:
            match = re.search(
                rf"/courses/{course_id}/questions/{question_id}/submissions/(\d+)/grade",
                resp.text,
            )
            if match:
                ctx = _get_grading_context(course_id, question_id, match.group(1))

        # Path 2: grade page (may redirect to a specific submission)
        if ctx is None:
            url2 = (
                f"{conn.gradescope_base_url}/courses/{course_id}"
                f"/questions/{question_id}/grade"
            )
            resp2 = conn.session.get(url2, allow_redirects=True)
            if resp2.status_code == 200:
                sub_match = re.search(
                    rf"/questions/{question_id}/submissions/(\d+)",
                    resp2.url,
                )
                if sub_match:
                    ctx = _get_grading_context(
                        course_id, question_id, sub_match.group(1)
                    )
                else:
                    # Check if the page itself has SubmissionGrader props
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp2.text, "html.parser")
                    grader = soup.find(attrs={"data-react-class": "SubmissionGrader"})
                    if grader:
                        import json as _json
                        props = _json.loads(grader.get("data-react-props", "{}"))
                        rubric_items = props.get("rubric_items", [])
                        if rubric_items:
                            question = props.get("question", {})
                            ctx = {"props": props}

        if ctx is None:
            return (
                f"No submissions found for question `{question_id}`. "
                "Cannot access rubric. The question may be in a special "
                "assignment type. Try using `get_submission_grading_context` "
                "with a known Question Submission ID instead."
            )
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching rubric: {e}"

    props = ctx["props"]
    question = props.get("question", {})
    # Use props.rubric_items (same source as grading context), fallback to question.rubric
    rubric_items = props.get("rubric_items", []) or question.get("rubric", [])

    if not rubric_items:
        return f"No rubric items found for question `{question_id}`. You can create them with `tool_create_rubric_item`."

    weight = question.get("weight", "?")
    scoring_type = question.get("scoring_type", "negative")

    lines = [f"## Rubric for Question `{question_id}`\n"]
    lines.append(f"**Weight:** {weight} pts")
    lines.append(f"**Scoring:** {scoring_type}\n")
    lines.append("| ID | Description | Points |")
    lines.append("|----|-------------|--------|")

    for item in rubric_items:
        desc = item.get("description", "(no description)")
        # Escape pipes in description
        desc = desc.replace("|", "\\|")
        lines.append(
            f"| `{item['id']}` | {desc} | {item.get('weight', 0)} |"
        )

    return "\n".join(lines)


def _fetch_question_submission_entries(
    course_id: str,
    question_id: str,
) -> list[dict[str, str | bool]]:
    """Return parsed question-submission entries from the submissions page."""
    conn = get_connection()
    url = (
        f"{conn.gradescope_base_url}/courses/{course_id}"
        f"/questions/{question_id}/submissions"
    )
    resp = conn.session.get(url)
    if resp.status_code != 200:
        raise ValueError(
            f"Cannot access submissions page for question "
            f"`{question_id}` (status {resp.status_code})."
        )

    soup = BeautifulSoup(resp.text, "html.parser")
    pattern = re.compile(
        rf"/courses/{re.escape(course_id)}/questions/{re.escape(question_id)}"
        rf"/submissions/(\d+)/grade"
    )

    seen = set()
    entries: list[dict[str, str | bool]] = []
    for link in soup.find_all("a", href=pattern):
        match = pattern.search(link.get("href", ""))
        if not match:
            continue
        sid = match.group(1)
        if sid in seen:
            continue
        seen.add(sid)

        row = link.find_parent("tr")
        student_name = ""
        if row:
            for td in row.find_all("td"):
                text = td.get_text(strip=True)
                if text and not text.startswith("/") and text != sid:
                    student_name = text
                    break

        graded = False
        if row:
            # Scan <td> cells in reverse to find the score cell, which is
            # typically the last or second-to-last column.  Checking cells
            # individually avoids false positives from student names or IDs
            # that happen to contain digits.
            score_pattern = re.compile(
                r"^\s*"
                r"(?:\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?"  # N/N or N.N/N.N
                r"|\d+(?:\.\d+)?"                         # plain number
                r"|Graded|✓|✅)"
                r"\s*$"
            )
            for td in reversed(row.find_all("td")):
                cell_text = td.get_text(strip=True)
                if cell_text and score_pattern.match(cell_text):
                    graded = True
                    break

        entries.append(
            {
                "submission_id": sid,
                "student_name": student_name,
                "graded": graded,
            }
        )

    entries.sort(key=lambda entry: int(str(entry["submission_id"])))
    return entries


def _fallback_next_ungraded_submission_id(
    course_id: str,
    question_id: str,
    current_sid: str,
) -> str | None:
    """Fallback path when navigation_urls.next_ungraded is missing or stale."""
    entries = _fetch_question_submission_entries(course_id, question_id)
    ungraded_ids = [
        str(entry["submission_id"])
        for entry in entries
        if not entry.get("graded", False)
    ]
    if not ungraded_ids:
        return None

    if current_sid and current_sid in ungraded_ids:
        current_index = ungraded_ids.index(current_sid)
        if current_index + 1 < len(ungraded_ids):
            return ungraded_ids[current_index + 1]
        return current_sid

    for sid in ungraded_ids:
        if not current_sid or int(sid) > int(current_sid):
            return sid
    return ungraded_ids[0]


def _try_fallback_navigation(
    course_id: str,
    question_id: str,
    current_sid: str,
    props: dict,
    output_format: str,
) -> str:
    """Attempt fallback navigation; always returns a displayable result string."""
    try:
        fallback_sid = _fallback_next_ungraded_submission_id(
            course_id, question_id, current_sid
        )
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: {e}"
    if fallback_sid is None:
        return "All submissions for this question are graded! 🎉"
    if fallback_sid == current_sid and not props.get("submission", {}).get("graded", False):
        return (
            "This is the only ungraded submission remaining for this "
            "question.  Grade it first, then call `get_next_ungraded` "
            "again to advance."
        )
    return get_submission_grading_context(
        course_id, question_id, fallback_sid, output_format
    )


def apply_grade(
    course_id: str,
    question_id: str,
    submission_id: str,
    rubric_item_ids: list[str] | None = None,
    point_adjustment: float | None = None,
    comment: str | None = None,
    confidence: float | None = None,
    confirm_write: bool = False,
) -> str:
    """Apply a grade to a student's question submission.

    This is the main grading tool. It can:
    1. Apply/remove rubric items (toggle which are checked)
    2. Set a submission-specific point adjustment
    3. Add a grader comment

    **WARNING**: This modifies student grades. Use with caution.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        submission_id: The question submission ID.
        rubric_item_ids: List of rubric item IDs to apply (checked). Items NOT in
            this list will be unchecked. Pass None to keep current rubric unchanged.
        point_adjustment: Submission-specific point adjustment (can be negative).
            Pass None to keep current adjustment unchanged.
        comment: Grader comment for this submission. Pass None to keep unchanged.
        confidence: Agent's self-assessed grading confidence (0.0-1.0).
            - < 0.6: Grade will be REJECTED — skip or flag for human review.
            - 0.6-0.8: Grade will proceed with a warning to review.
            - > 0.8: Grade proceeds normally.
            - None: No confidence gating (manual grading mode).
        confirm_write: Must be True to save the grade.
    """
    if not course_id or not question_id or not submission_id:
        return "Error: course_id, question_id, and submission_id are required."

    # Defensive coercion: MCP clients / LLMs sometimes pass a single string
    # instead of a list.  Wrap it so the rest of the logic works.
    if isinstance(rubric_item_ids, str):
        rubric_item_ids = [rubric_item_ids]

    if rubric_item_ids is None and point_adjustment is None and comment is None:
        return "Error: at least one of rubric_item_ids, point_adjustment, or comment must be provided."

    # Confidence gate: reject low-confidence grades
    if confidence is not None:
        if confidence < 0.0 or confidence > 1.0:
            return "Error: confidence must be between 0.0 and 1.0."
        if confidence < 0.6:
            return (
                f"⚠️ **Grade REJECTED** — Your confidence is `{confidence:.2f}` (below 0.6 threshold).\n"
                f"**Action:** Skip this submission or flag for human review.\n"
                f"- submission_id: `{submission_id}`\n"
                f"- Tip: If the handwriting is unclear or the answer is ambiguous, "
                f"move to the next submission with `tool_get_next_ungraded`."
            )

    try:
        ctx = _get_grading_context(course_id, question_id, submission_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching grading context: {e}"

    props = ctx["props"]
    session = ctx["session"]
    csrf_token = ctx["csrf_token"]
    base_url = ctx["base_url"]

    if not confirm_write:
        details = [
            f"course_id=`{course_id}`",
            f"question_id=`{question_id}`",
            f"submission_id=`{submission_id}`",
            f"current_score={props.get('submission', {}).get('score', 'Ungraded')}",
        ]
        if rubric_item_ids is not None:
            details.append(f"rubric_item_ids={sorted(rubric_item_ids)}")
        if point_adjustment is not None:
            details.append(f"point_adjustment={point_adjustment}")
        if comment is not None:
            details.append(f"comment={comment}")
        return write_confirmation_required("apply_grade", details)

    save_url = props.get("urls", {}).get("save_grade")
    if not save_url:
        return "Error: save_grade URL not found in grading context."

    # Build the payload
    # Gradescope expects a specific format for saving grades
    rubric_items = props.get("rubric_items", [])
    current_evals = props.get("rubric_item_evaluations", [])
    current_eval = props.get("evaluation", {})

    # Build rubric_item_ids_to_apply
    if rubric_item_ids is not None:
        # User specified which rubric items to apply
        apply_ids = set(rubric_item_ids)
    else:
        # Keep current state
        apply_ids = {str(e["rubric_item_id"]) for e in current_evals if e.get("present")}

    # Build the JSON payload matching what the Gradescope frontend sends.
    # Structure: {"rubric_items": {"ID": {"score": "true"/"false"}, ...},
    #             "question_submission_evaluation": {"points": ..., "comments": ...}}
    rubric_items_payload = {}
    for ri in rubric_items:
        rid = str(ri["id"])
        rubric_items_payload[rid] = {
            "score": "true" if rid in apply_ids else "false"
        }

    resolved_points = (
        point_adjustment if point_adjustment is not None
        else current_eval.get("points")
    )
    resolved_comments = (
        comment if comment is not None
        else current_eval.get("comments")
    )

    json_payload = {
        "rubric_items": rubric_items_payload,
        "question_submission_evaluation": {
            "points": resolved_points,
            "comments": resolved_comments,
        },
    }

    # POST to save_grade
    headers = {
        "X-CSRF-Token": csrf_token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = session.post(
            f"{base_url}{save_url}",
            json=json_payload,
            headers=headers,
        )
    except Exception as e:
        return f"Error saving grade: {e}"

    if resp.status_code == 200:
        # Parse response
        try:
            result = resp.json()
            new_score = result.get("score", "?")
            return (
                f"✅ Grade saved successfully!\n"
                f"**New score:** {new_score}/{props.get('question', {}).get('weight', '?')}\n"
                f"**Rubric items applied:** {list(apply_ids)}\n"
                f"**Point adjustment:** {point_adjustment}\n"
                f"**Comment:** {comment or '(unchanged)'}"
            )
        except Exception:
            return f"✅ Grade saved (status 200). Response: {resp.text[:200]}"
    else:
        return f"Error: Grade save failed (status {resp.status_code}). Response: {resp.text[:300]}"


def create_rubric_item(
    course_id: str,
    question_id: str,
    description: str,
    weight: float,
    confirm_write: bool = False,
) -> str:
    """Create a new rubric item for a question.

    **WARNING**: This modifies the rubric. Changes apply to ALL submissions.

    Weight is always a **positive** number. Gradescope uses the question's
    ``scoring_type`` to determine interpretation:
    - **Positive scoring:** Weight = points earned (e.g., ``5.0`` → student
      gets +5 when this item is applied).
    - **Negative scoring:** Weight = points deducted (e.g., ``2.0`` → student
      loses −2 when this item is applied). The web UI shows this as ``-2``.

    **Do NOT pass negative weight values.** Gradescope expects positive
    numbers and handles the sign internally based on ``scoring_type``.

    Check the scoring type with ``get_question_rubric`` or
    ``get_submission_grading_context`` before creating items.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        description: Description of the rubric item (e.g., "Correct answer").
        weight: Point value — always positive. See scoring-type note above.
        confirm_write: Must be True to create the rubric item.
    """
    if not course_id or not question_id or not description:
        return "Error: course_id, question_id, and description are required."

    if not confirm_write:
        return write_confirmation_required(
            "create_rubric_item",
            [
                f"course_id=`{course_id}`",
                f"question_id=`{question_id}`",
                f"description={description}",
                f"weight={weight}",
            ],
        )

    try:
        conn = get_connection()
        # Need CSRF token from any grading page for this question
        # Get the first submission page
        grade_url = (
            f"{conn.gradescope_base_url}/courses/{course_id}"
            f"/questions/{question_id}/submissions"
        )
        resp = conn.session.get(grade_url)
        if resp.status_code != 200:
            return f"Error accessing question page (status {resp.status_code})."

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_meta = soup.find("meta", {"name": "csrf-token"})
        csrf_token = csrf_meta.get("content", "") if csrf_meta else ""

    except AuthError as e:
        return f"Authentication error: {e}"
    except Exception as e:
        return f"Error: {e}"

    # POST to rubric_item endpoint
    rubric_url = (
        f"{conn.gradescope_base_url}/courses/{course_id}"
        f"/questions/{question_id}/rubric_items"
    )

    payload = {
        "rubric_item[description]": description,
        "rubric_item[weight]": str(weight),
    }

    headers = {
        "X-CSRF-Token": csrf_token,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = conn.session.post(rubric_url, data=payload, headers=headers)
    except Exception as e:
        return f"Error creating rubric item: {e}"

    if resp.status_code in (200, 201):
        try:
            result = resp.json()
            new_id = result.get("id", "?")
            return (
                f"✅ Rubric item created!\n"
                f"**ID:** `{new_id}`\n"
                f"**Description:** {description}\n"
                f"**Weight:** {weight}"
            )
        except Exception:
            return f"✅ Rubric item created (status {resp.status_code})."
    else:
        return f"Error: Failed to create rubric item (status {resp.status_code}). Response: {resp.text[:300]}"


def _find_question_submission_id(course_id: str, question_id: str) -> str:
    """Find a valid question submission ID by scraping the submissions page."""
    entries = _fetch_question_submission_entries(course_id, question_id)
    if not entries:
        raise ValueError(f"No submission found for question `{question_id}`.")
    return str(entries[0]["submission_id"])


def list_question_submissions(
    course_id: str, question_id: str, filter: str = "all",
) -> str:
    """List all Question Submission IDs for a question.

    This tool is essential for parallel grading: use it to pre-allocate
    specific submission IDs to subagents so they can grade independently
    without race conditions.

    Unlike ``get_assignment_submissions`` (which returns Global Submission
    IDs that grading tools cannot use), this returns **Question Submission
    IDs** that work directly with ``get_submission_grading_context`` and
    ``apply_grade``.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        filter: ``"all"`` (default), ``"ungraded"``, or ``"graded"``.

    Returns:
        JSON list of ``{submission_id, student_name, graded}`` entries
        for the requested question, sorted by submission ID.
    """
    if not course_id or not question_id:
        return "Error: course_id and question_id are required."

    if filter not in ("all", "ungraded", "graded"):
        return 'Error: filter must be "all", "ungraded", or "graded".'

    try:
        entries = _fetch_question_submission_entries(course_id, question_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching submissions page: {e}"

    if not entries:
        return f"No submissions found for question `{question_id}`."

    # Apply filter
    if filter == "ungraded":
        entries = [e for e in entries if not e["graded"]]
    elif filter == "graded":
        entries = [e for e in entries if e["graded"]]

    # Sort by submission_id
    entries.sort(key=lambda e: int(e["submission_id"]))

    summary = (
        f"Found {len(entries)} {'(' + filter + ') ' if filter != 'all' else ''}"
        f"submissions for question `{question_id}`."
    )

    return json.dumps({"summary": summary, "submissions": entries}, indent=2)


def get_next_ungraded(
    course_id: str, question_id: str, submission_id: str = "",
    output_format: str = "markdown",
) -> str:
    """Navigate to the next ungraded submission for the same question.

    Returns the grading context for the next ungraded submission,
    or a message if all submissions are graded.

    Args:
        course_id: The Gradescope course ID.
        question_id: The current question ID.
        submission_id: The current Question Submission ID (optional).
            If omitted or invalid, auto-discovers a valid submission.
            NOTE: This must be a Question Submission ID, not a Global
            Submission ID from get_assignment_submissions.
        output_format: "markdown" (default) or "json".
    """
    if not course_id or not question_id:
        return "Error: course_id and question_id are required."

    # Try the provided submission_id first; fall back to auto-discovery
    ctx = None
    if submission_id:
        try:
            ctx = _get_grading_context(course_id, question_id, submission_id)
        except ValueError as e:
            if "404" in str(e):
                # Likely a Global Submission ID — fall back to auto-discovery
                ctx = None
            else:
                return f"Error: {e}"
        except AuthError as e:
            return f"Authentication error: {e}"
        except Exception as e:
            return f"Error: {e}"

    if ctx is None:
        # Auto-discover a valid question submission ID
        try:
            auto_sid = _find_question_submission_id(course_id, question_id)
            ctx = _get_grading_context(course_id, question_id, auto_sid)
        except AuthError as e:
            return f"Authentication error: {e}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"

    props = ctx["props"]
    nav = props.get("navigation_urls", {})
    next_url = nav.get("next_ungraded", "")

    # Determine the current submission ID we're sitting on.
    # ALWAYS use the ID from the loaded context (props), not the original
    # input, because the input may be an invalid global submission ID that
    # was auto-discovered into a different question submission ID.
    current_sid = str(props.get("submission", {}).get("id", "")) or submission_id

    if not next_url:
        return _try_fallback_navigation(
            course_id, question_id, current_sid, props, output_format
        )

    # Extract IDs from the URL
    qid_m = re.search(r"/questions/(\d+)", next_url)
    sid_m = re.search(r"/submissions/(\d+)", next_url)

    if not qid_m or not sid_m:
        return f"Could not parse next ungraded URL: {next_url}"

    next_qid = qid_m.group(1)
    next_sid = sid_m.group(1)

    # Gradescope sets next_ungraded to the CURRENT submission when it is
    # itself ungraded.  The caller wants the NEXT one, so we must advance
    # past the current submission via next_submission.
    if next_sid == current_sid:
        next_sub_url = nav.get("next_submission", "")
        if not next_sub_url:
            return (
                "This is the only ungraded submission remaining for this "
                "question.  Grade it first, then call `get_next_ungraded` "
                "again to advance."
            )
        ns_qid_m = re.search(r"/questions/(\d+)", next_sub_url)
        ns_sid_m = re.search(r"/submissions/(\d+)", next_sub_url)
        if not ns_qid_m or not ns_sid_m:
            return f"Could not parse next_submission URL: {next_sub_url}"

        advance_qid = ns_qid_m.group(1)
        advance_sid = ns_sid_m.group(1)

        # Load the next submission to check whether it is ungraded
        try:
            advance_ctx = _get_grading_context(course_id, advance_qid, advance_sid)
        except Exception:
            # If we can't load it, just return it and let the caller deal
            return get_submission_grading_context(
                course_id, advance_qid, advance_sid, output_format
            )

        advance_sub = advance_ctx["props"].get("submission", {})
        if not advance_sub.get("graded", False):
            # It's ungraded — return this one
            return get_submission_grading_context(
                course_id, advance_qid, advance_sid, output_format
            )

        # It's graded — follow ITS next_ungraded (which should now
        # point to a genuinely different ungraded submission)
        adv_nav = advance_ctx["props"].get("navigation_urls", {})
        adv_next = adv_nav.get("next_ungraded", "")
        if adv_next:
            a_qid_m = re.search(r"/questions/(\d+)", adv_next)
            a_sid_m = re.search(r"/submissions/(\d+)", adv_next)
            if a_qid_m and a_sid_m:
                final_sid = a_sid_m.group(1)
                if final_sid != advance_sid:
                    return get_submission_grading_context(
                        course_id, a_qid_m.group(1), final_sid, output_format
                    )
        return _try_fallback_navigation(
            course_id, question_id, current_sid, props, output_format
        )

    # Normal case: next_ungraded points to a different submission
    return get_submission_grading_context(course_id, next_qid, next_sid, output_format)


def update_rubric_item(
    course_id: str,
    question_id: str,
    rubric_item_id: str,
    description: str | None = None,
    weight: float | None = None,
    confirm_write: bool = False,
) -> str:
    """Update an existing rubric item's description or weight.

    **WARNING**: Changes cascade to ALL submissions that have this item applied.
    Updating the weight will immediately change every affected student's score.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        rubric_item_id: The rubric item ID to update.
        description: New description, or None to keep unchanged.
        weight: New point value, or None to keep unchanged.
        confirm_write: Must be True to apply the update.
    """
    if not course_id or not question_id or not rubric_item_id:
        return "Error: course_id, question_id, and rubric_item_id are required."

    if description is None and weight is None:
        return "Error: at least one of description or weight must be provided."

    if not confirm_write:
        details = [
            f"course_id=`{course_id}`",
            f"question_id=`{question_id}`",
            f"rubric_item_id=`{rubric_item_id}`",
        ]
        if description is not None:
            details.append(f"new_description={description}")
        if weight is not None:
            details.append(f"new_weight={weight}")
        details.append("⚠️ This will affect ALL submissions with this rubric item applied.")
        return write_confirmation_required("update_rubric_item", details)

    try:
        conn = get_connection()
        # Get CSRF token from the question's submissions page
        subs_url = (
            f"{conn.gradescope_base_url}/courses/{course_id}"
            f"/questions/{question_id}/submissions"
        )
        resp = conn.session.get(subs_url)
        if resp.status_code != 200:
            return f"Error accessing question page (status {resp.status_code})."

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_meta = soup.find("meta", {"name": "csrf-token"})
        csrf_token = csrf_meta.get("content", "") if csrf_meta else ""
    except AuthError as e:
        return f"Authentication error: {e}"
    except Exception as e:
        return f"Error: {e}"

    # PUT to the rubric item endpoint
    update_url = (
        f"{conn.gradescope_base_url}/courses/{course_id}"
        f"/questions/{question_id}/rubric_items/{rubric_item_id}"
    )

    payload = {}
    if description is not None:
        payload["rubric_item[description]"] = description
    if weight is not None:
        payload["rubric_item[weight]"] = str(weight)

    headers = {
        "X-CSRF-Token": csrf_token,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = conn.session.put(update_url, data=payload, headers=headers)
    except Exception as e:
        return f"Error updating rubric item: {e}"

    if resp.status_code == 200:
        return (
            f"✅ Rubric item `{rubric_item_id}` updated!\n"
            f"**Description:** {description or '(unchanged)'}\n"
            f"**Weight:** {weight if weight is not None else '(unchanged)'}\n"
            f"⚠️ All submissions with this item have been recalculated."
        )
    else:
        return f"Error: Update failed (status {resp.status_code}). Response: {resp.text[:300]}"


def delete_rubric_item(
    course_id: str,
    question_id: str,
    rubric_item_id: str,
    confirm_write: bool = False,
) -> str:
    """Delete a rubric item from a question.

    **WARNING**: Deleting a rubric item removes it from ALL submissions.
    Any students who had this item applied will have their scores recalculated.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        rubric_item_id: The rubric item ID to delete.
        confirm_write: Must be True to delete the item.
    """
    if not course_id or not question_id or not rubric_item_id:
        return "Error: course_id, question_id, and rubric_item_id are required."

    if not confirm_write:
        return write_confirmation_required(
            "delete_rubric_item",
            [
                f"course_id=`{course_id}`",
                f"question_id=`{question_id}`",
                f"rubric_item_id=`{rubric_item_id}`",
                "⚠️ This permanently deletes the item and recalculates ALL affected scores.",
            ],
        )

    try:
        conn = get_connection()
        subs_url = (
            f"{conn.gradescope_base_url}/courses/{course_id}"
            f"/questions/{question_id}/submissions"
        )
        resp = conn.session.get(subs_url)
        if resp.status_code != 200:
            return f"Error accessing question page (status {resp.status_code})."

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_meta = soup.find("meta", {"name": "csrf-token"})
        csrf_token = csrf_meta.get("content", "") if csrf_meta else ""
    except AuthError as e:
        return f"Authentication error: {e}"
    except Exception as e:
        return f"Error: {e}"

    delete_url = (
        f"{conn.gradescope_base_url}/courses/{course_id}"
        f"/questions/{question_id}/rubric_items/{rubric_item_id}"
    )

    headers = {
        "X-CSRF-Token": csrf_token,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = conn.session.delete(delete_url, headers=headers)
    except Exception as e:
        return f"Error deleting rubric item: {e}"

    if resp.status_code in (200, 204):
        return (
            f"✅ Rubric item `{rubric_item_id}` deleted.\n"
            f"All affected submissions have been recalculated."
        )
    else:
        return f"Error: Delete failed (status {resp.status_code}). Response: {resp.text[:300]}"
