import datetime
import json
from types import SimpleNamespace

from gradescope_mcp.tools import assignments, grading_ops


def test_get_assignments_formats_rows(monkeypatch) -> None:
    sample_assignments = [
        SimpleNamespace(
            name="Homework 1",
            assignment_id="10",
            release_date=datetime.datetime(2026, 3, 1, 9, 30),
            due_date=datetime.datetime(2026, 3, 8, 23, 59),
            late_due_date=None,
            submissions_status="Submitted",
            grade="8.5",
            max_grade="10.0",
        ),
        SimpleNamespace(
            name="Quiz 1",
            assignment_id="11",
            release_date=None,
            due_date=None,
            late_due_date=None,
            submissions_status=None,
            grade=None,
            max_grade=None,
        ),
    ]
    fake_conn = SimpleNamespace(
        account=SimpleNamespace(get_assignments=lambda _course_id: sample_assignments)
    )
    monkeypatch.setattr(assignments, "get_connection", lambda: fake_conn)

    result = assignments.get_assignments("123")

    assert "## Assignments for Course 123" in result
    assert "| 1 | Homework 1 | `10` | 2026-03-01 09:30 | 2026-03-08 23:59 | N/A | Submitted | 8.5/10.0 |" in result
    assert "| 2 | Quiz 1 | `11` | N/A | N/A | N/A | N/A | N/A/N/A |" in result
    assert "**Total assignments:** 2" in result


def test_modify_assignment_dates_rejects_invalid_date() -> None:
    result = assignments.modify_assignment_dates(
        "1",
        "2",
        due_date="2026/03/20 12:00",
    )

    assert "Error: Invalid date format" in result
    assert "YYYY-MM-DDTHH:MM" in result


def test_rename_assignment_rejects_whitespace_title() -> None:
    result = assignments.rename_assignment("1", "2", "   ")
    assert result == "Error: new_title cannot be all whitespace."


def test_get_submission_grading_context_json_filters_self_links_and_placeholder_pages(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        grading_ops,
        "_get_grading_context",
        lambda *_args, **_kwargs: {
            "props": {
                "question": {
                    "title": "Q1",
                    "weight": 5,
                    "scoring_type": "positive",
                    "parameters": {
                        "crop_rect_list": [{"page_number": 2, "x1": 1, "x2": 2, "y1": 3, "y2": 4}]
                    },
                },
                "submission": {
                    "id": "99",
                    "owner_names": "Student A",
                    "score": 4.5,
                    "graded": True,
                    "answers": {
                        "text": "final answer",
                        "uploads": [{"text_file_id": "abc123"}],
                    },
                },
                "evaluation": {
                    "points": -0.5,
                    "comments": "Needs more detail",
                },
                "rubric_items": [
                    {"id": 10, "description": "Correct", "weight": 5, "position": 0},
                    {"id": 20, "description": "Style", "weight": -1, "position": 1, "locked": True},
                ],
                "rubric_item_evaluations": [
                    {"rubric_item_id": 10, "present": True},
                    {"rubric_item_id": 20, "present": False},
                ],
                "navigation_urls": {
                    "previous_ungraded": "/courses/1/questions/2/submissions/88/grade",
                    "next_ungraded": "/courses/1/questions/2/submissions/99/grade",
                    "next_submission": "/courses/1/questions/2/submissions/100/grade",
                    "next_question": "/courses/1/questions/3/submissions/101/grade",
                },
                "num_graded_submissions": 4,
                "num_submissions": 9,
                "groups_present": True,
                "answer_group": 7,
                "answer_group_size": 3,
                "pages": [
                    {"number": 1, "url": "//example.com/page1.jpg"},
                    {"number": 2, "url": "https://example.com/missing_pdf.png"},
                    {"number": 3, "url": ""},
                    {"number": 4, "url": "https://example.com/page4.jpg"},
                ],
            }
        },
    )

    result = grading_ops.get_submission_grading_context("1", "2", "99", output_format="json")
    parsed = json.loads(result)

    assert parsed["student"] == "Student A"
    assert parsed["text_answer"] == "final answer\n[Uploaded file ID: abc123]"
    assert parsed["navigation"] == {
        "previous_ungraded": {"question_id": "2", "submission_id": "88"},
        "next_submission": {"question_id": "2", "submission_id": "100"},
        "next_question": {"question_id": "3", "submission_id": "101"},
    }
    assert parsed["rubric_items"][0]["applied"] is True
    assert parsed["rubric_items"][1]["locked"] is True
    assert parsed["answer_group"] == {"id": "7", "size": 3, "groups_present": True}
    assert parsed["pages"] == [
        {"number": 1, "url": "https://example.com/page1.jpg"},
        {"number": 4, "url": "https://example.com/page4.jpg"},
    ]
    assert parsed["crop_regions"] == [{"page_number": 2, "x1": 1, "x2": 2, "y1": 3, "y2": 4}]


def test_get_submission_grading_context_markdown_shows_real_pages_only(monkeypatch) -> None:
    monkeypatch.setattr(
        grading_ops,
        "_get_grading_context",
        lambda *_args, **_kwargs: {
            "props": {
                "question": {
                    "title": "Integral",
                    "weight": 10,
                    "scoring_type": "negative",
                    "floor": 0,
                    "ceiling": 10,
                    "parameters": {"crop_rect_list": [{"page_number": 3}]},
                },
                "submission": {
                    "owner_names": "Student B",
                    "score": None,
                    "graded": False,
                    "answers": {},
                },
                "evaluation": {},
                "rubric_items": [],
                "rubric_item_evaluations": [],
                "navigation_urls": {
                    "next_submission": "/courses/1/questions/2/submissions/100/grade",
                },
                "num_graded_submissions": 1,
                "num_submissions": 5,
                "pages": [
                    {"number": 1, "url": "https://example.com/missing_pdf.png"},
                    {"number": 2, "url": "https://example.com/page2.jpg"},
                    {"number": 3, "url": "https://example.com/page3.jpg"},
                    {"number": 4, "url": "https://example.com/page4.jpg"},
                    {"number": 5, "url": "https://example.com/page5.jpg"},
                ],
            }
        },
    )

    result = grading_ops.get_submission_grading_context("1", "2", "99")

    assert "## Grading Context — QIntegral" in result
    assert "**Scoring:** negative (floor=0, ceiling=10)" in result
    assert "Rubric items **deduct** points" in result
    assert "- **next_submission**: qid=`2`, sid=`100`" in result
    assert "**Relevant pages:** [3]" in result
    assert "- Page 2: [View](https://example.com/page2.jpg)" in result
    assert "- _...and 1 more pages_" in result
    assert "missing_pdf" not in result


def test_get_question_rubric_uses_question_rubric_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        grading_ops,
        "get_connection",
        lambda: SimpleNamespace(
            gradescope_base_url="https://example.com",
            session=SimpleNamespace(
                get=lambda *_args, **_kwargs: SimpleNamespace(
                    status_code=200,
                    text='<a href="/courses/1/questions/2/submissions/777/grade">grade</a>',
                    url="https://example.com/courses/1/questions/2/grade",
                )
            ),
        ),
    )
    monkeypatch.setattr(
        grading_ops,
        "_get_grading_context",
        lambda *_args, **_kwargs: {
            "props": {
                "question": {
                    "weight": 7,
                    "scoring_type": "negative",
                    "rubric": [
                        {"id": 55, "description": "Needs | escaping", "weight": -2},
                    ],
                },
                "rubric_items": [],
            }
        },
    )

    result = grading_ops.get_question_rubric("1", "2")

    assert "## Rubric for Question `2`" in result
    assert "**Weight:** 7 pts" in result
    assert "**Scoring:** negative" in result
    assert "| `55` | Needs \\| escaping | -2 |" in result


def test_apply_grade_sends_json_payload(monkeypatch) -> None:
    """Verify apply_grade sends JSON (not form-encoded) matching Gradescope's format."""
    captured = {}

    class FakeSession:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"score": 4.0},
                text='{"score": 4.0}',
            )

    monkeypatch.setattr(
        grading_ops,
        "_get_grading_context",
        lambda *_args, **_kwargs: {
            "props": {
                "question": {"title": "Q1", "weight": 5},
                "submission": {"score": None, "graded": False},
                "evaluation": {"points": None, "comments": None},
                "rubric_items": [
                    {"id": 100, "description": "Correct", "weight": 5},
                    {"id": 200, "description": "Partial", "weight": 2},
                ],
                "rubric_item_evaluations": [],
                "urls": {"save_grade": "/courses/1/questions/2/submissions/99/save_grade"},
            },
            "csrf_token": "test-csrf-token",
            "session": FakeSession(),
            "base_url": "https://example.com",
        },
    )

    result = grading_ops.apply_grade(
        course_id="1",
        question_id="2",
        submission_id="99",
        rubric_item_ids=["100"],
        point_adjustment=None,
        comment="Good work",
        confirm_write=True,
    )

    assert "Grade saved" in result

    # Verify JSON payload structure
    payload = captured["kwargs"]["json"]
    assert "rubric_items" in payload
    assert "question_submission_evaluation" in payload
    assert payload["rubric_items"]["100"] == {"score": "true"}
    assert payload["rubric_items"]["200"] == {"score": "false"}
    assert payload["question_submission_evaluation"]["comments"] == "Good work"
    assert payload["question_submission_evaluation"]["points"] is None

    # Verify Content-Type header
    headers = captured["kwargs"]["headers"]
    assert headers["Content-Type"] == "application/json"

    # Verify json= was used (not data=)
    assert "data" not in captured["kwargs"]


def test_positive_scoring_context_shows_add_hint(monkeypatch) -> None:
    """Positive scoring should show 'items add points' hint."""
    monkeypatch.setattr(
        grading_ops,
        "_get_grading_context",
        lambda *_args, **_kwargs: {
            "props": {
                "question": {
                    "title": "MCQ",
                    "weight": 4,
                    "scoring_type": "positive",
                },
                "submission": {
                    "owner_names": "Student C",
                    "score": None,
                    "graded": False,
                    "answers": {},
                },
                "evaluation": {},
                "rubric_items": [
                    {"id": 1, "description": "Correct", "weight": 4},
                ],
                "rubric_item_evaluations": [],
                "navigation_urls": {},
                "num_graded_submissions": 0,
                "num_submissions": 10,
                "pages": [],
            }
        },
    )

    result = grading_ops.get_submission_grading_context("1", "2", "99")
    assert "Rubric items **add** points" in result
    assert "deduct" not in result.lower()


def test_get_next_ungraded_falls_back_to_submission_listing_when_nav_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        grading_ops,
        "_get_grading_context",
        lambda *_args, **_kwargs: {
            "props": {
                "submission": {"id": "99", "graded": True},
                "navigation_urls": {},
                "num_graded_submissions": 4,
                "num_submissions": 5,
            }
        },
    )
    monkeypatch.setattr(
        grading_ops,
        "_fetch_question_submission_entries",
        lambda *_args, **_kwargs: [
            {"submission_id": "90", "student_name": "A", "graded": True},
            {"submission_id": "99", "student_name": "B", "graded": True},
            {"submission_id": "105", "student_name": "C", "graded": False},
        ],
    )
    monkeypatch.setattr(
        grading_ops,
        "get_submission_grading_context",
        lambda course_id, question_id, submission_id, output_format="markdown": (
            f"CTX {course_id} {question_id} {submission_id} {output_format}"
        ),
    )

    result = grading_ops.get_next_ungraded("1", "2", "99", output_format="json")

    assert result == "CTX 1 2 105 json"


def test_list_question_submissions_ungraded_filter_excludes_integer_scored_rows(
    monkeypatch,
) -> None:
    submissions_html = """
    <table>
      <tr>
        <td>Alice Example</td>
        <td>5</td>
        <td><a href="/courses/1/questions/2/submissions/101/grade">grade</a></td>
      </tr>
      <tr>
        <td>Bob Example</td>
        <td>—</td>
        <td><a href="/courses/1/questions/2/submissions/102/grade">grade</a></td>
      </tr>
    </table>
    """
    monkeypatch.setattr(
        grading_ops,
        "get_connection",
        lambda: SimpleNamespace(
            gradescope_base_url="https://example.com",
            session=SimpleNamespace(
                get=lambda *_args, **_kwargs: SimpleNamespace(
                    status_code=200,
                    text=submissions_html,
                )
            ),
        ),
    )

    result = json.loads(grading_ops.list_question_submissions("1", "2", filter="ungraded"))

    assert [sub["submission_id"] for sub in result["submissions"]] == ["102"]


def test_graded_heuristic_matches_fractional_and_integer_scores(monkeypatch) -> None:
    """The heuristic should detect N/N scores, integer scores, and not false-positive on names."""
    submissions_html = """
    <table>
      <tr>
        <td>Alice 3rd</td>
        <td>8/10</td>
        <td><a href="/courses/1/questions/2/submissions/201/grade">grade</a></td>
      </tr>
      <tr>
        <td>Bob Example</td>
        <td>5</td>
        <td><a href="/courses/1/questions/2/submissions/202/grade">grade</a></td>
      </tr>
      <tr>
        <td>Carol Example</td>
        <td>—</td>
        <td><a href="/courses/1/questions/2/submissions/203/grade">grade</a></td>
      </tr>
      <tr>
        <td>Dave 42nd</td>
        <td></td>
        <td><a href="/courses/1/questions/2/submissions/204/grade">grade</a></td>
      </tr>
    </table>
    """
    monkeypatch.setattr(
        grading_ops,
        "get_connection",
        lambda: SimpleNamespace(
            gradescope_base_url="https://example.com",
            session=SimpleNamespace(
                get=lambda *_args, **_kwargs: SimpleNamespace(
                    status_code=200,
                    text=submissions_html,
                )
            ),
        ),
    )

    entries = grading_ops._fetch_question_submission_entries("1", "2")
    graded_map = {e["submission_id"]: e["graded"] for e in entries}

    # "8/10" → graded
    assert graded_map["201"] is True
    # "5" → graded (integer score)
    assert graded_map["202"] is True
    # "—" → not graded
    assert graded_map["203"] is False
    # empty → not graded (name "Dave 42nd" must not false-positive)
    assert graded_map["204"] is False


def test_get_next_ungraded_uses_props_sid_after_auto_discovery(monkeypatch) -> None:
    """After auto-discovery, current_sid should come from props, not the stale input."""
    call_log = []

    def fake_get_grading_context(course_id, question_id, submission_id):
        call_log.append(("ctx", submission_id))
        if submission_id == "GLOBAL_99999":
            raise ValueError("404 Not Found")
        return {
            "props": {
                "submission": {"id": "200", "graded": True},
                "navigation_urls": {
                    # next_ungraded points to a different sid than the discovered one
                    "next_ungraded": f"/courses/{course_id}/questions/{question_id}/submissions/300/grade",
                },
                "num_graded_submissions": 4,
                "num_submissions": 5,
            }
        }

    monkeypatch.setattr(grading_ops, "_get_grading_context", fake_get_grading_context)
    monkeypatch.setattr(
        grading_ops,
        "_find_question_submission_id",
        lambda *_args, **_kwargs: "200",
    )
    monkeypatch.setattr(
        grading_ops,
        "get_submission_grading_context",
        lambda course_id, question_id, submission_id, output_format="markdown": (
            f"CTX {course_id} {question_id} {submission_id} {output_format}"
        ),
    )

    # Pass a global submission ID that will 404
    result = grading_ops.get_next_ungraded("1", "2", "GLOBAL_99999", output_format="json")

    # next_ungraded points to 300, which is different from props sid 200,
    # so it should navigate to 300
    assert result == "CTX 1 2 300 json"

