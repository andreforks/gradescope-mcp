"""Tests for answer_groups tools."""

from gradescope_mcp.tools import answer_groups


def test_get_answer_groups_requires_ids() -> None:
    result = answer_groups.get_answer_groups("", "123")
    assert "Error" in result

    result = answer_groups.get_answer_groups("123", "")
    assert "Error" in result


def test_get_answer_group_detail_requires_ids() -> None:
    result = answer_groups.get_answer_group_detail("", "123", "456")
    assert "Error" in result


def test_grade_answer_group_requires_confirm(monkeypatch) -> None:
    """grade_answer_group should refuse without confirm_write."""
    monkeypatch.setattr(
        answer_groups,
        "_fetch_answer_groups_json",
        lambda *_args, **_kwargs: {
            "groups": [{"id": 3, "title": "test group"}],
            "submissions": [
                {"confirmed_group_id": 3, "graded": False},
                {"confirmed_group_id": 3, "graded": True},
            ],
        },
    )
    # Mock get_connection for the group grade page request
    import types

    class _FakeResp:
        status_code = 200
        text = '<html><meta name="csrf-token" content="tok"><div data-react-class="SubmissionGrader" data-react-props=\'{"urls":{"save_grade":"/courses/1/questions/2/submissions/100/save_grade"},"rubric_items":[{"id":10}],"rubric_item_evaluations":[],"evaluation":{}}\'></div></html>'

    class _FakeSession:
        def get(self, url, **kw):
            return _FakeResp()

    class _FakeConn:
        gradescope_base_url = "https://example.com"
        session = _FakeSession()

    monkeypatch.setattr(answer_groups, "get_connection", lambda: _FakeConn())

    result = answer_groups.grade_answer_group(
        "1", "2", "3",
        rubric_item_ids=["10"],
        comment="test",
    )
    assert "Write confirmation required" in result
    assert "grade_answer_group" in result
    assert "group_size=2 submissions" in result
    assert "confirm_write=True" in result

