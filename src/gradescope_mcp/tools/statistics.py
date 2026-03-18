"""Assignment statistics tools.

These tools provide read access to assignment-level and per-question statistics
from the Gradescope `/statistics.json` endpoint.
"""

import json
import logging

from gradescope_mcp.auth import get_connection, AuthError

logger = logging.getLogger(__name__)


def get_assignment_statistics(course_id: str, assignment_id: str) -> str:
    """Get comprehensive statistics for an assignment.

    Returns assignment-level summary (mean, median, min, max, std) and
    per-question breakdowns showing average scores, standard deviations,
    and number of graded submissions. Requires instructor/TA access.

    Args:
        course_id: The Gradescope course ID.
        assignment_id: The assignment ID.
    """
    if not course_id or not assignment_id:
        return "Error: course_id and assignment_id are required."

    try:
        conn = get_connection()
        url = (
            f"{conn.gradescope_base_url}/courses/{course_id}"
            f"/assignments/{assignment_id}/statistics.json"
        )
        resp = conn.session.get(url)
    except AuthError as e:
        return f"Authentication error: {e}"
    except Exception as e:
        return f"Error fetching statistics: {e}"

    if resp.status_code != 200:
        return f"Error: Cannot access statistics (status {resp.status_code})."

    try:
        data = resp.json()
    except Exception:
        return "Error: Failed to parse statistics response."

    info = data.get("assignment_statistics_info", {})
    if not info:
        return "No statistics data available for this assignment."

    # Assignment metadata
    assignment = info.get("assignment", {})
    title = assignment.get("title", f"Assignment {assignment_id}")
    try:
        total_points = float(assignment.get("totalPoints", 0))
    except (ValueError, TypeError):
        total_points = 0.0
    fully_graded = info.get("assignmentFullyGraded", False)

    lines = [f"## Statistics — {title}\n"]
    lines.append(f"**Total points:** {total_points}")
    lines.append(f"**Fully graded:** {'Yes' if fully_graded else 'No'}")

    # Assignment-level summary
    summary = info.get("summaryStatistics", {}).get("assignment", {})
    if summary:
        mean_pct = summary.get("mean", 0) * 100
        median_pct = summary.get("median", 0) * 100
        min_pct = summary.get("min", 0) * 100
        max_pct = summary.get("max", 0) * 100
        std_pct = summary.get("standardDeviation", 0) * 100

        lines.append(f"\n### Overall Performance")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Mean | {mean_pct:.1f}% ({mean_pct * total_points / 100:.1f}/{total_points}) |")
        lines.append(f"| Median | {median_pct:.1f}% ({median_pct * total_points / 100:.1f}/{total_points}) |")
        lines.append(f"| Min | {min_pct:.1f}% |")
        lines.append(f"| Max | {max_pct:.1f}% |")
        lines.append(f"| Std Dev | {std_pct:.1f}% |")

        if summary.get("reliability") and summary["reliability"] != "--":
            lines.append(f"| Reliability | {summary['reliability']} |")

    # Per-question statistics
    q_stats = info.get("summaryStatistics", {}).get("questions", {})
    q_avgs = info.get("questionAverages", [])

    if q_avgs:
        lines.append(f"\n### Per-Question Averages\n")
        lines.append("| Question | Weight | Mean% | Graded | Min% | Max% | StdDev% |")
        lines.append("|----------|--------|-------|--------|------|------|---------|")

        for q_label, q_avg_pct in q_avgs:
            # Find matching question stats
            # q_label is like "1.1", "1.2", etc.
            lines.append(f"| {q_label} | — | {q_avg_pct:.1f}% | — | — | — | — |")

    # Better: use the questions dict which has more detail
    if q_stats:
        lines.pop()  # Remove the less-detailed table if it was added
        # Rebuild with detail
        lines_to_remove = 0
        for line in reversed(lines):
            if line.startswith("| Question") or line.startswith("|----") or line.startswith("| "):
                lines_to_remove += 1
            else:
                break
        if lines_to_remove > 0:
            lines = lines[:-lines_to_remove]

        lines.append(f"\n### Per-Question Statistics\n")
        lines.append("| Question | Weight | Mean% | Graded | StdDev% |")
        lines.append("|----------|--------|-------|--------|---------|")

        # Sort by question title in a sensible way
        sorted_qs = sorted(
            q_stats.items(),
            key=lambda x: x[1].get("title", ""),
        )

        for qid, qs in sorted_qs:
            q_title = qs.get("title", qid)
            weight = qs.get("weight", "?")
            mean_pct = qs.get("mean", 0) * 100
            graded = qs.get("graded", 0)
            std_pct = qs.get("standardDeviation", 0) * 100

            lines.append(
                f"| {q_title} | {weight} | {mean_pct:.1f}% | {graded} | {std_pct:.1f}% |"
            )

    # Identify struggling questions (mean < 70%)
    struggling = []
    for qid, qs in q_stats.items():
        mean = qs.get("mean", 1.0)
        if mean < 0.7:
            struggling.append((qs.get("title", qid), mean * 100, qs.get("weight", 0)))

    if struggling:
        lines.append(f"\n### ⚠️ Low-Scoring Questions (< 70% avg)")
        for title, mean, weight in sorted(struggling, key=lambda x: x[1]):
            lines.append(f"- **{title}** ({weight} pts): {mean:.1f}% average")

    return "\n".join(lines)
