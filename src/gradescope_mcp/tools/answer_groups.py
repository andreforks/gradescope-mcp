"""Answer group tools for batch grading.

Gradescope's AI-Assisted Grading clusters similar answers into groups.
Instead of grading N submissions individually, a TA grades one group and
the score is applied to all members at once via the `save_many_grades`
endpoint.

These tools expose answer groups to AI agents so that:
1. The agent can list groups for a question and see their titles/sizes.
2. The agent can inspect a group's representative answer.
3. The agent can batch-grade an entire group in one call.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from gradescope_mcp.auth import AuthError, get_connection
from gradescope_mcp.tools.safety import write_confirmation_required

logger = logging.getLogger(__name__)


def _fetch_answer_groups_json(
    course_id: str, question_id: str
) -> dict[str, Any]:
    """Fetch the answer groups JSON for a question."""
    conn = get_connection()
    url = (
        f"{conn.gradescope_base_url}/courses/{course_id}"
        f"/questions/{question_id}/answer_groups"
    )
    resp = conn.session.get(
        url,
        headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    if resp.status_code == 401:
        raise ValueError(
            f"Cannot access answer groups (status 401 Unauthorized). "
            "Possible causes:\n"
            "  1. AI-Assisted Grading is not enabled for this question\n"
            "  2. Insufficient permissions (requires instructor/TA role)\n"
            "  3. This question type does not support answer groups"
        )
    if resp.status_code != 200:
        raise ValueError(
            f"Cannot access answer groups (status {resp.status_code}). "
            "Check that AI-Assisted Grading is enabled for this question."
        )
    return resp.json()


def get_answer_groups(
    course_id: str,
    question_id: str,
    output_format: str = "markdown",
) -> str:
    """List all answer groups for a question.

    Answer groups cluster similar student answers together for efficient
    batch grading. Instead of grading each submission individually, you
    can grade one group and the score applies to all members.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        output_format: "markdown" (default) or "json" for structured output.
    """
    if not course_id or not question_id:
        return "Error: course_id and question_id are required."

    try:
        data = _fetch_answer_groups_json(course_id, question_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching answer groups: {e}"

    groups = data.get("groups", [])
    submissions = data.get("submissions", [])
    question = data.get("question", {})
    status = data.get("status", "unknown")

    # Count submissions per group
    group_counts: dict[int, dict[str, int]] = {}
    for sub in submissions:
        gid = sub.get("confirmed_group_id") or sub.get("unconfirmed_group_id")
        if gid is not None:
            if gid not in group_counts:
                group_counts[gid] = {"total": 0, "graded": 0}
            group_counts[gid]["total"] += 1
            if sub.get("graded"):
                group_counts[gid]["graded"] += 1

    # Count ungrouped
    ungrouped = [
        s for s in submissions
        if not s.get("confirmed_group_id") and not s.get("unconfirmed_group_id")
    ]

    if output_format == "json":
        result = {
            "question_id": question_id,
            "question_title": question.get("numbered_title", ""),
            "assisted_grading_type": question.get("assisted_grading_type"),
            "status": status,
            "num_groups": len(groups),
            "num_submissions": len(submissions),
            "num_ungrouped": len(ungrouped),
            "groups": [],
        }
        for g in groups:
            gid = g["id"]
            counts = group_counts.get(gid, {"total": 0, "graded": 0})
            result["groups"].append({
                "id": str(gid),
                "title": g.get("title", ""),
                "size": counts["total"],
                "graded": counts["graded"],
                "hidden": g.get("hidden", False),
                "question_type": g.get("question_type", ""),
            })
        return json.dumps(result, indent=2)

    # Markdown output
    lines = [
        f"## Answer Groups — {question.get('numbered_title', question_id)}",
        f"**Type:** {question.get('assisted_grading_type', 'unknown')}",
        f"**Status:** {status}",
        f"**Total:** {len(submissions)} submissions across {len(groups)} groups"
        + (f" + {len(ungrouped)} ungrouped" if ungrouped else ""),
        "",
        "| # | Group ID | Title | Size | Graded | Hidden |",
        "|---|----------|-------|------|--------|--------|",
    ]

    for i, g in enumerate(groups, 1):
        gid = g["id"]
        counts = group_counts.get(gid, {"total": 0, "graded": 0})
        title = g.get("title", "(untitled)")
        # Truncate long LaTeX titles
        if len(title) > 60:
            title = title[:57] + "..."
        hidden = "🙈" if g.get("hidden") else ""
        graded_str = f"{counts['graded']}/{counts['total']}"
        lines.append(
            f"| {i} | `{gid}` | {title} | {counts['total']} | {graded_str} | {hidden} |"
        )

    if ungrouped:
        lines.append(f"\n**Ungrouped:** {len(ungrouped)} submissions need manual grouping")

    return "\n".join(lines)


def get_answer_group_detail(
    course_id: str,
    question_id: str,
    group_id: str,
    output_format: str = "markdown",
) -> str:
    """Get detailed information about a specific answer group.

    Shows the group's title, member submissions, graded status, and
    representative crop images. Use this to understand what answers
    are in a group before batch-grading.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        group_id: The answer group ID (from get_answer_groups).
        output_format: "markdown" (default) or "json" for structured output.
    """
    if not course_id or not question_id or not group_id:
        return "Error: course_id, question_id, and group_id are required."

    try:
        data = _fetch_answer_groups_json(course_id, question_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error fetching answer group detail: {e}"

    groups = data.get("groups", [])
    submissions = data.get("submissions", [])

    # Find the target group
    target_group = None
    for g in groups:
        if str(g["id"]) == str(group_id):
            target_group = g
            break

    if not target_group:
        return f"Error: group `{group_id}` not found. Use get_answer_groups to list available groups."

    # Filter submissions in this group
    group_subs = [
        s for s in submissions
        if str(s.get("confirmed_group_id")) == str(group_id)
        or str(s.get("unconfirmed_group_id")) == str(group_id)
    ]

    if output_format == "json":
        result = {
            "group_id": str(group_id),
            "title": target_group.get("title", ""),
            "question_type": target_group.get("question_type", ""),
            "hidden": target_group.get("hidden", False),
            "size": len(group_subs),
            "graded_count": sum(1 for s in group_subs if s.get("graded")),
            "submissions": [
                {
                    "submission_id": str(s["id"]),
                    "assignment_submission_id": str(s.get("assignment_submission_id", "")),
                    "graded": s.get("graded", False),
                    "graded_individually": s.get("graded_individually", False),
                    "confirmed": str(s.get("confirmed_group_id")) == str(group_id),
                    "inferred_answer": s.get("inferred_answer"),
                    "masked_crop": s.get("masked_crop"),
                }
                for s in group_subs
            ],
        }
        return json.dumps(result, indent=2)

    # Markdown output
    graded_count = sum(1 for s in group_subs if s.get("graded"))
    confirmed_count = sum(
        1 for s in group_subs
        if str(s.get("confirmed_group_id")) == str(group_id)
    )

    lines = [
        f"## Answer Group Detail — `{group_id}`",
        f"**Title:** {target_group.get('title', '(untitled)')}",
        f"**Type:** {target_group.get('question_type', 'unknown')}",
        f"**Size:** {len(group_subs)} submissions ({confirmed_count} confirmed, "
        f"{len(group_subs) - confirmed_count} inferred)",
        f"**Graded:** {graded_count}/{len(group_subs)}",
        "",
    ]

    # Show representative crops
    crops_shown = 0
    for s in group_subs[:3]:
        crop = s.get("masked_crop")
        if crop and isinstance(crop, dict) and crop.get("url"):
            lines.append(f"**Sample crop (sub `{s['id']}`):** [View]({crop['url']})")
            crops_shown += 1

    if crops_shown == 0:
        # Show inferred answers instead
        answers_shown = set()
        for s in group_subs[:5]:
            ans = s.get("inferred_answer")
            if ans and ans not in answers_shown:
                lines.append(f"**Inferred answer:** {ans}")
                answers_shown.add(ans)

    lines.append("")
    lines.append("### Submissions")
    lines.append("| # | Submission ID | Graded | Individually | Confirmed |")
    lines.append("|---|---------------|--------|-------------|-----------|")

    for i, s in enumerate(group_subs[:20], 1):
        graded = "✅" if s.get("graded") else "—"
        individual = "✏️" if s.get("graded_individually") else "—"
        confirmed = "✅" if str(s.get("confirmed_group_id")) == str(group_id) else "🤖"
        lines.append(f"| {i} | `{s['id']}` | {graded} | {individual} | {confirmed} |")

    if len(group_subs) > 20:
        lines.append(f"| ... | _{len(group_subs) - 20} more_ | | | |")

    lines.append(f"\nTo batch-grade this group, use `grade_answer_group` with group_id=`{group_id}`.")

    return "\n".join(lines)


def grade_answer_group(
    course_id: str,
    question_id: str,
    group_id: str,
    rubric_item_ids: list[str] | None = None,
    point_adjustment: float | None = None,
    comment: str | None = None,
    confirm_write: bool = False,
) -> str:
    """Batch-grade all submissions in an answer group at once.

    This is the most efficient grading method. Instead of grading N
    submissions individually, you grade one group and the score applies
    to ALL members via the `save_many_grades` endpoint.

    **WARNING**: This modifies grades for ALL submissions in the group.

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID.
        group_id: The answer group ID.
        rubric_item_ids: Rubric item IDs to apply. None keeps current.
        point_adjustment: Submission-specific point adjustment. None keeps current.
        comment: Grader comment. None keeps current.
        confirm_write: Must be True to apply grades.
    """
    if not course_id or not question_id or not group_id:
        return "Error: course_id, question_id, and group_id are required."

    if rubric_item_ids is None and point_adjustment is None and comment is None:
        return "Error: at least one of rubric_item_ids, point_adjustment, or comment must be provided."

    try:
        conn = get_connection()

        # Get answer groups data for group size info
        ag_data = _fetch_answer_groups_json(course_id, question_id)
        group_subs = [
            s for s in ag_data.get("submissions", [])
            if str(s.get("confirmed_group_id")) == str(group_id)
            or str(s.get("unconfirmed_group_id")) == str(group_id)
        ]
        target_group = None
        for g in ag_data.get("groups", []):
            if str(g["id"]) == str(group_id):
                target_group = g
                break

        if not target_group:
            return f"Error: group `{group_id}` not found."

        # Access the group grading page to get save_many_grades URL + CSRF
        group_grade_url = (
            f"{conn.gradescope_base_url}/courses/{course_id}"
            f"/questions/{question_id}/answer_groups/{group_id}/grade"
        )
        resp = conn.session.get(group_grade_url)
        if resp.status_code != 200:
            return f"Error: Cannot access group grade page (status {resp.status_code})."

        soup = BeautifulSoup(resp.text, "html.parser")
        csrf_meta = soup.find("meta", {"name": "csrf-token"})
        csrf_token = csrf_meta.get("content", "") if csrf_meta else ""

        grader = soup.find(attrs={"data-react-class": "SubmissionGrader"})
        if not grader:
            return "Error: SubmissionGrader component not found on group grade page."

        props = json.loads(grader.get("data-react-props", "{}"))

    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error preparing batch grade: {e}"

    # Confirmation gate
    if not confirm_write:
        details = [
            f"course_id=`{course_id}`",
            f"question_id=`{question_id}`",
            f"group_id=`{group_id}`",
            f"group_title={target_group.get('title', '?')}",
            f"group_size={len(group_subs)} submissions",
        ]
        if rubric_item_ids is not None:
            details.append(f"rubric_item_ids={sorted(rubric_item_ids)}")
        if point_adjustment is not None:
            details.append(f"point_adjustment={point_adjustment}")
        if comment is not None:
            details.append(f"comment={comment}")
        return write_confirmation_required("grade_answer_group", details)

    # Find save_many_grades URL
    save_url = props.get("urls", {}).get("save_grade")
    if not save_url:
        return "Error: save_grade URL not found in group grading context."

    # In group mode, the URL should be save_many_grades
    # The frontend replaces save_grade with save_many_grades when group_mode=True
    save_many_url = save_url.replace("/save_grade", "/save_many_grades")
    if "/save_many_grades" not in save_many_url:
        # Fallback: construct it
        match = re.search(r"/submissions/(\d+)", save_url)
        if match:
            save_many_url = save_url.replace(
                f"/submissions/{match.group(1)}/save_grade",
                f"/submissions/{match.group(1)}/save_many_grades",
            )

    # Build payload
    rubric_items = props.get("rubric_items", [])
    current_evals = props.get("rubric_item_evaluations", [])
    current_eval = props.get("evaluation", {})

    if rubric_item_ids is not None:
        apply_ids = set(str(rid) for rid in rubric_item_ids)
    else:
        apply_ids = {str(e["rubric_item_id"]) for e in current_evals if e.get("present")}

    payload = {}
    for ri in rubric_items:
        rid = str(ri["id"])
        present = rid in apply_ids
        payload[f"rubric_item_ids[{rid}]"] = "true" if present else "false"

    if point_adjustment is not None:
        payload["points"] = str(point_adjustment)
    elif current_eval.get("points") is not None:
        payload["points"] = str(current_eval["points"])

    if comment is not None:
        payload["comment"] = comment
    elif current_eval.get("comments"):
        payload["comment"] = current_eval["comments"]

    headers = {
        "X-CSRF-Token": csrf_token,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = conn.session.post(
            f"{conn.gradescope_base_url}{save_many_url}",
            data=payload,
            headers=headers,
        )
    except Exception as e:
        return f"Error saving batch grade: {e}"

    if resp.status_code == 200:
        try:
            result = resp.json()
            return (
                f"✅ Batch grade applied to {len(group_subs)} submissions!\n"
                f"**Group:** {target_group.get('title', group_id)}\n"
                f"**Rubric items applied:** {sorted(apply_ids)}\n"
                f"**Point adjustment:** {point_adjustment}\n"
                f"**Comment:** {comment or '(unchanged)'}"
            )
        except Exception:
            return f"✅ Batch grade saved (status 200). Response: {resp.text[:200]}"
    else:
        return (
            f"Error: Batch grade failed (status {resp.status_code}). "
            f"Response: {resp.text[:300]}"
        )
