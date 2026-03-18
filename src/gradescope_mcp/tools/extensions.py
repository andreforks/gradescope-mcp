"""Extension management MCP tools (instructor/TA only)."""

import datetime

from gradescopeapi.classes.extensions import (
    get_extensions as gs_get_extensions,
    update_student_extension,
)

from gradescope_mcp.auth import get_connection, AuthError
from gradescope_mcp.tools.safety import write_confirmation_required


def _format_datetime(dt: datetime.datetime | None) -> str:
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def get_extensions(course_id: str, assignment_id: str) -> str:
    """Get all extensions for a specific assignment.

    Args:
        course_id: The Gradescope course ID.
        assignment_id: The assignment ID.
    """
    if not course_id or not assignment_id:
        return "Error: both course_id and assignment_id are required."

    try:
        conn = get_connection()
        extensions = gs_get_extensions(
            session=conn.session,
            course_id=course_id,
            assignment_id=assignment_id,
        )
    except AuthError as e:
        return f"Authentication error: {e}"
    except Exception as e:
        return f"Error fetching extensions: {e}"

    if not extensions:
        return f"No extensions found for assignment `{assignment_id}` in course `{course_id}`."

    lines = [f"## Extensions for Assignment {assignment_id}\n"]
    lines.append("| User ID | Name | Release Date | Due Date | Late Due Date |")
    lines.append("|---------|------|-------------|----------|---------------|")

    for user_id, ext in extensions.items():
        lines.append(
            f"| `{user_id}` | {ext.name} | "
            f"{_format_datetime(ext.release_date)} | "
            f"{_format_datetime(ext.due_date)} | "
            f"{_format_datetime(ext.late_due_date)} |"
        )

    lines.append(f"\n**Total extensions:** {len(extensions)}")
    return "\n".join(lines)


def set_extension(
    course_id: str,
    assignment_id: str,
    user_id: str,
    release_date: str | None = None,
    due_date: str | None = None,
    late_due_date: str | None = None,
    confirm_write: bool = False,
) -> str:
    """Add or update an extension for a student on an assignment.

    Args:
        course_id: The Gradescope course ID.
        assignment_id: The assignment ID.
        user_id: The student's Gradescope user ID. Use get_course_roster to find user IDs.
        release_date: Extension release date in ISO format (YYYY-MM-DDTHH:MM), or None.
        due_date: Extension due date in ISO format (YYYY-MM-DDTHH:MM), or None.
        late_due_date: Extension late due date in ISO format (YYYY-MM-DDTHH:MM), or None.
        confirm_write: Must be True to perform the update.
    """
    if not all([course_id, assignment_id, user_id]):
        return "Error: course_id, assignment_id, and user_id are all required."

    if not any([release_date, due_date, late_due_date]):
        return "Error: at least one date must be provided."

    def parse_date(date_str: str | None) -> datetime.datetime | None:
        if date_str is None:
            return None
        try:
            return datetime.datetime.fromisoformat(date_str)
        except ValueError:
            raise ValueError(f"Invalid date format: '{date_str}'. Use ISO format: YYYY-MM-DDTHH:MM")

    try:
        rd = parse_date(release_date)
        dd = parse_date(due_date)
        ldd = parse_date(late_due_date)
    except ValueError as e:
        return f"Error: {e}"

    if not confirm_write:
        details = [
            f"course_id=`{course_id}`",
            f"assignment_id=`{assignment_id}`",
            f"user_id=`{user_id}`",
        ]
        if release_date:
            details.append(f"release_date={release_date}")
        if due_date:
            details.append(f"due_date={due_date}")
        if late_due_date:
            details.append(f"late_due_date={late_due_date}")
        return write_confirmation_required("set_extension", details)

    try:
        conn = get_connection()
        success = update_student_extension(
            session=conn.session,
            course_id=course_id,
            assignment_id=assignment_id,
            user_id=user_id,
            release_date=rd,
            due_date=dd,
            late_due_date=ldd,
        )
    except AuthError as e:
        return f"Authentication error: {e}"
    except ValueError as e:
        return f"Validation error: {e}"
    except Exception as e:
        return f"Error setting extension: {e}"

    if success:
        updates = []
        if release_date:
            updates.append(f"Release date → {release_date}")
        if due_date:
            updates.append(f"Due date → {due_date}")
        if late_due_date:
            updates.append(f"Late due date → {late_due_date}")
        return (
            f"✅ Extension for user `{user_id}` on assignment `{assignment_id}` updated:\n"
            + "\n".join(f"- {u}" for u in updates)
        )
    else:
        return f"❌ Failed to set extension. Check your permissions and verify the user ID."
