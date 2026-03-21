from types import SimpleNamespace

from gradescope_mcp.tools import extensions, grading_workflow


def test_get_extensions_surfaces_401_from_underlying_library(monkeypatch) -> None:
    fake_conn = SimpleNamespace(session=object())
    monkeypatch.setattr(extensions, "get_connection", lambda: fake_conn)

    def _raise_401(**_kwargs):
        raise RuntimeError(
            "Failed to get extensions for assignment 7607880. Status code: 401"
        )

    monkeypatch.setattr(extensions, "gs_get_extensions", _raise_401)

    result = extensions.get_extensions("1205064", "7607880")

    assert "Extensions are not available for assignment" in result
    assert "7607880" in result
    assert "scanned PDF" in result


def test_prepare_answer_key_reports_missing_reference_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        grading_workflow,
        "_fetch_assignment_questions",
        lambda *_args, **_kwargs: {
            "101": {
                "index": 1,
                "title": "Question 1",
                "weight": 5.0,
                "type": "FreeResponseQuestion",
                "parent_id": None,
            },
            "102": {
                "index": 2,
                "title": "Question 2",
                "weight": 3.0,
                "type": "FreeResponseQuestion",
                "parent_id": None,
            },
        },
    )
    monkeypatch.setattr(
        grading_workflow,
        "_get_outline_data",
        lambda *_args, **_kwargs: {
            "assignment": {"title": "Demo Exam"},
            "questions": {
                "101": {"content": []},
                "102": {"content": []},
            },
        },
    )

    result = grading_workflow.prepare_answer_key("878373", "5030457")
    artifact_path = grading_workflow.get_artifact_path(
        "gradescope-answerkey-5030457.md"
    )
    artifact_text = artifact_path.read_text(encoding="utf-8")

    assert "✅ Grading basis prepared for **Demo Exam**" in result
    assert "- Questions with instructor reference answers: 0" in result
    assert "- Missing reference answers: 2 (Q1, Q2)" in result
    assert "- **⚠️ Missing answers:** Q1, Q2" in artifact_text
    assert artifact_text.startswith("# Grading Basis: Demo Exam")
    assert artifact_text.count("No instructor-provided reference answer is available for this question.") == 2
    assert "Do not treat this file as a true answer key here" in artifact_text
