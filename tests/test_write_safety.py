from pathlib import Path

from gradescope_mcp.tools import assignments, extensions, grading_ops, submissions


def test_upload_submission_requires_absolute_path(tmp_path: Path) -> None:
    relative = tmp_path.name
    result = submissions.upload_submission("1", "2", [relative], confirm_write=True)
    assert "file path must be absolute" in result


def test_upload_submission_requires_confirm(tmp_path: Path) -> None:
    file_path = tmp_path / "submission.txt"
    file_path.write_text("hello", encoding="utf-8")

    result = submissions.upload_submission("1", "2", [str(file_path)])

    assert "Write confirmation required" in result
    assert "No changes were made." in result
    assert "confirm_write=True" in result


def test_modify_assignment_dates_requires_confirm() -> None:
    result = assignments.modify_assignment_dates(
        "1",
        "2",
        due_date="2026-03-20T12:00",
    )

    assert "Write confirmation required" in result
    assert "modify_assignment_dates" in result
    assert "due_date=2026-03-20T12:00" in result


def test_set_extension_requires_confirm() -> None:
    result = extensions.set_extension(
        "1",
        "2",
        "3",
        due_date="2026-03-20T12:00",
    )

    assert "Write confirmation required" in result
    assert "set_extension" in result
    assert "user_id=`3`" in result


def test_apply_grade_requires_confirm(monkeypatch) -> None:
    monkeypatch.setattr(
        grading_ops,
        "_get_grading_context",
        lambda *_args, **_kwargs: {
            "props": {
                "submission": {"score": 4.0},
            },
            "session": object(),
            "csrf_token": "token",
            "base_url": "https://example.com",
        },
    )

    result = grading_ops.apply_grade(
        "1",
        "2",
        "3",
        rubric_item_ids=["10", "20"],
        point_adjustment=-1.0,
        comment="Needs revision",
    )

    assert "Write confirmation required" in result
    assert "apply_grade" in result
    assert "current_score=4.0" in result
    assert "point_adjustment=-1.0" in result


def test_create_rubric_item_requires_confirm() -> None:
    result = grading_ops.create_rubric_item("1", "2", "Missing proof", -2.0)

    assert "Write confirmation required" in result
    assert "create_rubric_item" in result
    assert "weight=-2.0" in result
