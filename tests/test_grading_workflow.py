from gradescope_mcp.tools import grading_workflow


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url: str):
        self.urls.append(url)
        return _FakeResponse(b"image-bytes")


class _FakeConn:
    def __init__(self, session: _FakeSession):
        self.session = session


def test_cache_relevant_pages_uses_authenticated_session(monkeypatch) -> None:
    session = _FakeSession()
    monkeypatch.setattr(grading_workflow, "_get_grading_context", lambda *_args, **_kwargs: {
        "props": {
            "question": {
                "parameters": {
                    "crop_rect_list": [{"page_number": 2, "x1": 0, "x2": 100, "y1": 0, "y2": 100}]
                }
            },
            "pages": [
                {"number": 1, "url": "https://example.com/1.jpg"},
                {"number": 2, "url": "https://example.com/2.jpg"},
                {"number": 3, "url": "https://example.com/3.jpg"},
            ],
        }
    })
    monkeypatch.setattr(grading_workflow, "get_connection", lambda: _FakeConn(session))

    result = grading_workflow.cache_relevant_pages("1", "test-assign", "test-question", "test-submission")

    assert "Cached 3 relevant page(s)" in result
    assert session.urls == [
        "https://example.com/1.jpg",
        "https://example.com/2.jpg",
        "https://example.com/3.jpg",
    ]
    assert (
        grading_workflow.pathlib.Path("/tmp/gradescope-pages-test-assign-test-question-test-submission/page_2.jpg")
        .read_bytes()
        == b"image-bytes"
    )
