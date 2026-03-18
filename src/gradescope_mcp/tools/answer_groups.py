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


def _partition_group_submissions(
    submissions: list[dict[str, Any]],
    group_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a group's members into confirmed and inferred submissions."""
    confirmed = [
        s for s in submissions
        if str(s.get("confirmed_group_id")) == str(group_id)
    ]
    inferred = [
        s for s in submissions
        if str(s.get("confirmed_group_id")) != str(group_id)
        and str(s.get("unconfirmed_group_id")) == str(group_id)
    ]
    return confirmed, inferred


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
        confirmed_gid = sub.get("confirmed_group_id")
        inferred_gid = sub.get("unconfirmed_group_id")

        if confirmed_gid is not None:
            if confirmed_gid not in group_counts:
                group_counts[confirmed_gid] = {"total": 0, "graded": 0, "inferred": 0}
            group_counts[confirmed_gid]["total"] += 1
            if sub.get("graded"):
                group_counts[confirmed_gid]["graded"] += 1

        if inferred_gid is not None and inferred_gid != confirmed_gid:
            if inferred_gid not in group_counts:
                group_counts[inferred_gid] = {"total": 0, "graded": 0, "inferred": 0}
            group_counts[inferred_gid]["inferred"] += 1

    # Count ungrouped
    ungrouped = [
        s for s in submissions
        if not s.get("confirmed_group_id") and not s.get("unconfirmed_group_id")
    ]

    if output_format == "json":
        manual_grouping_recommended = (
            question.get("assisted_grading_type") == "not_grouped" or len(groups) == 0
        )
        result = {
            "question_id": question_id,
            "question_title": question.get("numbered_title", ""),
            "assisted_grading_type": question.get("assisted_grading_type"),
            "status": status,
            "num_groups": len(groups),
            "num_submissions": len(submissions),
            "num_ungrouped": len(ungrouped),
            "grouping_available": len(groups) > 0,
            "manual_grouping_recommended": manual_grouping_recommended,
            "recommended_strategy": (
                "manual_sampling" if manual_grouping_recommended else "answer_groups"
            ),
            "groups": [],
        }
        for g in groups:
            gid = g["id"]
            counts = group_counts.get(gid, {"total": 0, "graded": 0, "inferred": 0})
            result["groups"].append({
                "id": str(gid),
                "title": g.get("title", ""),
                "size": counts["total"],
                "graded": counts["graded"],
                "inferred": counts["inferred"],
                "hidden": g.get("hidden", False),
                "question_type": g.get("question_type", ""),
            })
        return json.dumps(result, indent=2)

    # Markdown output
    ag_type = question.get('assisted_grading_type')
    # Resolve type: use assisted_grading_type first, fall back to per-group types
    if not ag_type and groups:
        group_types = {g.get('question_type', '') for g in groups if g.get('question_type')}
        ag_type = ', '.join(sorted(group_types)) if group_types else None
    ag_type_display = ag_type or '(not set)'
    group_word = 'group' if len(groups) == 1 else 'groups'
    lines = [
        f"## Answer Groups — {question.get('numbered_title', question_id)}",
        f"**Type:** {ag_type_display}",
        f"**Status:** {status}",
        f"**Total:** {len(submissions)} submissions across {len(groups)} {group_word}"
        + (f" + {len(ungrouped)} ungrouped" if ungrouped else ""),
        "",
        "| # | Group ID | Title | Type | Size | Graded | Hidden |",
        "|---|----------|-------|------|------|--------|--------|",
    ]

    for i, g in enumerate(groups, 1):
        gid = g["id"]
        counts = group_counts.get(gid, {"total": 0, "graded": 0, "inferred": 0})
        title = g.get("title", "(untitled)")
        # Truncate long LaTeX titles
        if len(title) > 60:
            title = title[:57] + "..."
        g_type = g.get("question_type", "") or ""
        hidden = "🙈" if g.get("hidden") else ""
        graded_str = f"{counts['graded']}/{counts['total']}"
        lines.append(
            f"| {i} | `{gid}` | {title} | {g_type} | {counts['total']} | {graded_str} | {hidden} |"
        )
        if counts["inferred"]:
            lines.append(
                f"|   |  | inferred members excluded from batch writes |  | +{counts['inferred']} |  |  |"
            )

    if ungrouped:
        lines.append(f"\n**Ungrouped:** {len(ungrouped)} submissions need manual grouping")
    if question.get("assisted_grading_type") == "not_grouped" or len(groups) == 0:
        lines.append(
            "\n**Recommendation:** Gradescope has no usable answer groups for this "
            "question. Fall back to manual sampling with "
            "`tool_list_question_submissions` and build your own grouping plan."
        )

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
    confirmed_subs, inferred_subs = _partition_group_submissions(submissions, group_id)

    if output_format == "json":
        def _sub_entry(s: dict[str, Any]) -> dict[str, Any]:
            return {
                "submission_id": str(s["id"]),
                "assignment_submission_id": str(s.get("assignment_submission_id", "")),
                "graded": s.get("graded", False),
                "graded_individually": s.get("graded_individually", False),
                "inferred_answer": s.get("inferred_answer"),
                "masked_crop": s.get("masked_crop"),
            }

        result = {
            "group_id": str(group_id),
            "title": target_group.get("title", ""),
            "question_type": target_group.get("question_type", ""),
            "hidden": target_group.get("hidden", False),
            "size": len(confirmed_subs),
            "inferred_count": len(inferred_subs),
            "graded_count": sum(
                1 for s in confirmed_subs + inferred_subs if s.get("graded")
            ),
            "submissions": [_sub_entry(s) for s in confirmed_subs],
            "inferred_submissions": [_sub_entry(s) for s in inferred_subs],
        }
        if inferred_subs:
            result["inferred_warning"] = (
                f"Gradescope's save_many_grades endpoint may also apply the "
                f"grade to {len(inferred_subs)} inferred (unconfirmed) member(s). "
                f"Review the inferred_submissions list before batch grading."
            )
        return json.dumps(result, indent=2)

    # Markdown output
    group_subs = confirmed_subs + inferred_subs
    graded_count = sum(1 for s in group_subs if s.get("graded"))

    lines = [
        f"## Answer Group Detail — `{group_id}`",
        f"**Title:** {target_group.get('title', '(untitled)')}",
        f"**Type:** {target_group.get('question_type', 'unknown')}",
        f"**Confirmed:** {len(confirmed_subs)} submissions",
        f"**Inferred:** {len(inferred_subs)} submissions",
        f"**Graded:** {graded_count}/{len(group_subs)}",
        "",
    ]
    if inferred_subs:
        lines.append(
            "⚠️ **Warning:** This group has inferred (unconfirmed) members. "
            "`save_many_grades` may apply the grade to them as well. "
            "Review the inferred members before batch grading."
        )
        lines.append("")

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

    # Defensive coercion: MCP clients / LLMs sometimes pass a single string
    # instead of a list.  Wrap it so the rest of the logic works.
    if isinstance(rubric_item_ids, str):
        rubric_item_ids = [rubric_item_ids]

    if rubric_item_ids is None:
        return (
            "Error: rubric_item_ids must be explicitly specified for batch grading. "
            "Passing None would inherit the sample submission's rubric state and "
            "propagate it to the entire group. Use get_answer_group_detail to "
            "inspect the group, then provide the exact rubric item IDs to apply."
        )

    if not rubric_item_ids and point_adjustment is None and comment is None:
        return "Error: at least one of rubric_item_ids, point_adjustment, or comment must be provided."

    try:
        conn = get_connection()

        # Get answer groups data for group size info
        ag_data = _fetch_answer_groups_json(course_id, question_id)
        group_subs, inferred_subs = _partition_group_submissions(
            ag_data.get("submissions", []), group_id
        )
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
            f"group_size={len(group_subs)} confirmed submissions",
        ]
        if inferred_subs:
            details.append(
                f"⚠️ inferred_members={len(inferred_subs)} — "
                f"save_many_grades may also apply to these unconfirmed members"
            )
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

    # Build JSON payload matching what the Gradescope frontend sends.
    # SAFETY: rubric_item_ids=None was already rejected above.
    # We never inherit current_evals from the sample submission.
    rubric_items = props.get("rubric_items", [])
    apply_ids = set(str(rid) for rid in rubric_item_ids)

    rubric_items_payload = {}
    for ri in rubric_items:
        rid = str(ri["id"])
        rubric_items_payload[rid] = {
            "score": "true" if rid in apply_ids else "false"
        }

    # SAFETY: Never inherit points/comments from the sample submission's
    # current_eval. Only include values the caller explicitly provided.
    evaluation_payload: dict[str, Any] = {}
    if point_adjustment is not None:
        evaluation_payload["points"] = point_adjustment
    if comment is not None:
        evaluation_payload["comments"] = comment

    json_payload = {
        "rubric_items": rubric_items_payload,
        "question_submission_evaluation": evaluation_payload,
    }

    headers = {
        "X-CSRF-Token": csrf_token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = conn.session.post(
            f"{conn.gradescope_base_url}{save_many_url}",
            json=json_payload,
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
