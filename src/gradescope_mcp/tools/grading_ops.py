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
        raise ValueError(f"Cannot access grading page (status {resp.status_code}).")

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

    # Navigation parsing
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
                nav_parsed[label] = {
                    "question_id": qid_m.group(1),
                    "submission_id": sid_m.group(1),
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
            "scoring_type": question.get("scoring_type", "positive"),
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
                {"number": p.get("number"), "url": p.get("url")}
                for p in pages if isinstance(p, dict) and p.get("url")
            ][:5],
            "crop_regions": crop,
        }
        return json.dumps(result, indent=2)

    # Markdown output
    lines = [f"## Grading Context — Q{question.get('title', '?')}"]
    lines.append(f"**Question ID:** `{question_id}` | **Submission ID:** `{submission_id}`")
    lines.append(f"**Student:** {submission.get('owner_names', 'Unknown')}")
    lines.append(f"**Weight:** {question.get('weight', '?')} pts")
    lines.append(f"**Current Score:** {submission.get('score', 'Ungraded')}")
    lines.append(f"**Graded:** {submission.get('graded', False)}")

    # Scoring type
    scoring_type = question.get("scoring_type", "positive")
    lines.append(f"**Scoring:** {scoring_type} (floor={question.get('floor')}, ceiling={question.get('ceiling')})")

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
            if isinstance(p, dict) and p.get("url"):
                lines.append(f"- Page {p.get('number', '?')}: [View]({p['url']})")
        if len(pages) > 3:
            lines.append(f"- _...and {len(pages) - 3} more pages_")

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
        # Find any submission for this question
        url = (
            f"{conn.gradescope_base_url}/courses/{course_id}"
            f"/questions/{question_id}/submissions"
        )
        resp = conn.session.get(url)
        if resp.status_code != 200:
            return f"Error: Cannot access submissions for question `{question_id}` (status {resp.status_code})."

        match = re.search(
            rf"/courses/{course_id}/questions/{question_id}/submissions/(\d+)/grade",
            resp.text,
        )
        if not match:
            return f"No submissions found for question `{question_id}`. Rubric may not exist yet."

        sub_id = match.group(1)
        ctx = _get_grading_context(course_id, question_id, sub_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching rubric: {e}"

    question = ctx["props"].get("question", {})
    rubric_items = question.get("rubric", [])

    if not rubric_items:
        return f"No rubric items found for question `{question_id}`. You can create them with `tool_create_rubric_item`."

    weight = question.get("weight", "?")
    scoring_type = question.get("scoring_type", "positive")

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

    # Build the payload matching what the frontend sends
    payload = {}

    # Rubric evaluations
    for ri in rubric_items:
        rid = str(ri["id"])
        present = rid in apply_ids
        payload[f"rubric_item_ids[{rid}]"] = "true" if present else "false"

    # Point adjustment
    if point_adjustment is not None:
        payload["points"] = str(point_adjustment)
    elif current_eval.get("points") is not None:
        payload["points"] = str(current_eval["points"])

    # Comment
    if comment is not None:
        payload["comment"] = comment
    elif current_eval.get("comments"):
        payload["comment"] = current_eval["comments"]

    # POST to save_grade
    headers = {
        "X-CSRF-Token": csrf_token,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = session.post(
            f"{base_url}{save_url}",
            data=payload,
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

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        description: Description of the rubric item (e.g., "Missing explanation").
        weight: Point value (positive for bonus, negative/0 for deductions).
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


def get_next_ungraded(
    course_id: str, question_id: str, submission_id: str
) -> str:
    """Navigate to the next ungraded submission for the same question.

    Returns the grading context for the next ungraded submission,
    or a message if all submissions are graded.

    Args:
        course_id: The Gradescope course ID.
        question_id: The current question ID.
        submission_id: The current submission ID.
    """
    if not course_id or not question_id or not submission_id:
        return "Error: course_id, question_id, and submission_id are required."

    try:
        ctx = _get_grading_context(course_id, question_id, submission_id)
    except (AuthError, ValueError, Exception) as e:
        return f"Error: {e}"

    nav = ctx["props"].get("navigation_urls", {})
    next_url = nav.get("next_ungraded", "")

    if not next_url:
        return "All submissions for this question are graded! 🎉"

    # Extract IDs from the URL
    qid_m = re.search(r"/questions/(\d+)", next_url)
    sid_m = re.search(r"/submissions/(\d+)", next_url)

    if not qid_m or not sid_m:
        return f"Could not parse next ungraded URL: {next_url}"

    next_qid = qid_m.group(1)
    next_sid = sid_m.group(1)

    # Return the grading context for the next submission
    return get_submission_grading_context(course_id, next_qid, next_sid)


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
