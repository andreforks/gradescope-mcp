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
