"""Submission-related MCP tools."""

import pathlib

from gradescopeapi.classes.upload import upload_assignment

from gradescope_mcp.auth import get_connection, AuthError
from gradescope_mcp.tools.grading import get_student_submission_content
from gradescope_mcp.tools.safety import write_confirmation_required


def upload_submission(
    course_id: str,
    assignment_id: str,
    file_paths: list[str],
    leaderboard_name: str | None = None,
    confirm_write: bool = False,
) -> str:
    """Upload files as a submission to a Gradescope assignment.

    Args:
        course_id: The Gradescope course ID.
        assignment_id: The assignment ID.
        file_paths: List of absolute file paths to upload.
        leaderboard_name: Optional leaderboard display name.
        confirm_write: Must be True to perform the upload.
    """
    if not course_id or not assignment_id:
        return "Error: both course_id and assignment_id are required."

    if not file_paths:
        return "Error: at least one file path is required."

    # Validate file paths
    validated_paths = []
    for fp in file_paths:
        original_path = pathlib.Path(fp)
        if not original_path.is_absolute():
            return f"Error: file path must be absolute: {fp}"

        path = original_path.resolve()

        if not path.exists():
            return f"Error: file not found: {fp}"

        if not path.is_file():
            return f"Error: not a file: {fp}"

        validated_paths.append(path)

    if not confirm_write:
        details = [
            f"course_id=`{course_id}`",
            f"assignment_id=`{assignment_id}`",
            f"files={', '.join(str(path) for path in validated_paths)}",
        ]
        if leaderboard_name:
            details.append(f"leaderboard_name={leaderboard_name}")
        return write_confirmation_required("upload_submission", details)

    try:
        conn = get_connection()
        file_handles = []
        try:
            for path in validated_paths:
                file_handles.append(open(path, "rb"))

            result_url = upload_assignment(
                session=conn.session,
                course_id=course_id,
                assignment_id=assignment_id,
                *file_handles,
                leaderboard_name=leaderboard_name,
            )
        finally:
            for fh in file_handles:
                fh.close()

    except AuthError as e:
        return f"Authentication error: {e}"
    except Exception as e:
        return f"Error uploading submission: {e}"

    if result_url:
        filenames = [p.name for p in validated_paths]
        return (
            f"✅ Submission uploaded successfully!\n"
            f"- **Files:** {', '.join(filenames)}\n"
            f"- **Submission URL:** {result_url}"
        )
    else:
        return (
            "❌ Upload failed. Possible reasons:\n"
            "- Assignment is past the due date\n"
            "- You don't have permission to submit\n"
            "- Invalid course or assignment ID"
        )


def get_assignment_submissions(course_id: str, assignment_id: str) -> str:
    """Get all submissions for an assignment (instructor/TA only).

    Returns a list of submission IDs and their file links.
    Note: This makes one request per submission and may be slow for large classes.

    Args:
        course_id: The Gradescope course ID.
        assignment_id: The assignment ID.
    """
    if not course_id or not assignment_id:
        return "Error: both course_id and assignment_id are required."

    try:
        conn = get_connection()
        submissions = conn.account.get_assignment_submissions(course_id, assignment_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except Exception as e:
        return f"Error fetching submissions: {e}"

    if not submissions:
        return f"No submissions found for assignment `{assignment_id}` in course `{course_id}`."

    lines = [f"## Submissions for Assignment {assignment_id}\n"]
    lines.append(f"**Total submissions:** {len(submissions)}\n")
    lines.append("| # | Submission ID | Files |")
    lines.append("|---|---------------|-------|")

    for i, (sub_id, links) in enumerate(submissions.items(), 1):
        file_count = len(links) if links else 0
        lines.append(f"| {i} | `{sub_id}` | {file_count} file(s) |")

    return "\n".join(lines)


def get_student_submission(
    course_id: str, assignment_id: str, student_email: str
) -> str:
    """Get the full content of a specific student's submission.

    Requires instructor/TA access. Returns the student's text answers for each
    question, as well as direct URLs to any uploaded files or images.

    Args:
        course_id: The Gradescope course ID.
        assignment_id: The assignment ID.
        student_email: The student's email address.
    """
    if not course_id or not assignment_id or not student_email:
        return "Error: course_id, assignment_id, and student_email are required."

    return get_student_submission_content(course_id, assignment_id, student_email)


def get_assignment_graders(course_id: str, question_id: str) -> str:
    """Get the list of graders for a specific question (instructor/TA only).

    Args:
        course_id: The Gradescope course ID.
        question_id: The question ID within the assignment.
    """
    if not course_id or not question_id:
        return "Error: both course_id and question_id are required."

    try:
        conn = get_connection()
        graders = conn.account.get_assignment_graders(course_id, question_id)
    except AuthError as e:
        return f"Authentication error: {e}"
    except Exception as e:
        return f"Error fetching graders: {e}"

    if not graders:
        return f"No graders found for question `{question_id}` in course `{course_id}`."

    lines = [
        f"## Graders for Question {question_id}\n",
        f"**Total graders:** {len(graders)}\n",
    ]
    for grader in sorted(graders):
        lines.append(f"- {grader}")

    return "\n".join(lines)
