"""Covers chat.py's streaming behavior: an ordinary (no web_search) answer
must stream live, word-by-word, as the model generates it — not be
buffered and sent as one chunk at the end. A search-triggered answer
must still suppress the model's own narration text around the tool
call, exactly as before this change, sent as a single final chunk."""
import re


def register(client, email="student@utep.edu"):
    from conftest import mark_email_verified
    resp = client.post("/register", data={
        "email": email, "password": "password123",
        "first_name": "Ada", "last_name": "Lovelace",
        "classification": "Senior", "major": "Computer Science", "university": "University of Texas at El Paso",
        "terms_agree": "on", "research_agree": "on", "age_confirm": "on",
    })
    mark_email_verified(email)
    return resp


class FakeContentBlock:
    def __init__(self, type_, text=None, name=None, input=None, content=None):
        self.type = type_
        self.text = text
        self.name = name
        self.input = input
        self.content = content


class FakeSearchResult:
    def __init__(self, title, url):
        self.title = title
        self.url = url


class FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class FakeFinalMessage:
    def __init__(self, content):
        self.content = content
        self.usage = FakeUsage()


class FakeEvent:
    def __init__(self, type_, content_block=None, text=None):
        self.type = type_
        self.content_block = content_block
        self.text = text


class FakeStream:
    def __init__(self, events, final_content):
        self._events = events
        self._final_content = final_content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return FakeFinalMessage(self._final_content)


class FakeMessages:
    def __init__(self, stream_obj):
        self._stream_obj = stream_obj

    def stream(self, **kwargs):
        return self._stream_obj


class FakeAnthropicClient:
    def __init__(self, stream_obj):
        self.messages = FakeMessages(stream_obj)


def _chat_csrf_token(client):
    r = client.get("/chat-page")
    return re.search(r'name="csrf-token" content="([^"]+)"', r.data.decode()).group(1)


def _chunks(resp):
    return [c.decode() if isinstance(c, bytes) else c for c in resp.response]


class TestChatStreaming:
    def test_ordinary_answer_streams_incrementally(self, client, app, monkeypatch):
        import wink.blueprints.chat as chat_module
        import wink.config as config

        register(client)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        csrf_token = _chat_csrf_token(client)

        events = [
            FakeEvent("content_block_start", content_block=FakeContentBlock("text")),
            FakeEvent("text", text="Hello "),
            FakeEvent("text", text="world!"),
        ]
        final_content = [FakeContentBlock("text", text="Hello world!")]
        monkeypatch.setattr(chat_module, "anthropic_client", FakeAnthropicClient(FakeStream(events, final_content)))

        resp = client.post("/chat", json={"message": "hi"}, headers={"X-CSRFToken": csrf_token})
        chunks = _chunks(resp)
        assert chunks == ["Hello ", "world!"], (
            f"expected two separate incremental chunks (true streaming), got {chunks}"
        )

    def test_search_triggered_answer_suppresses_narration(self, client, app, monkeypatch):
        """The model's own narration around a web_search call ('Let me
        check on that...') must never reach the student — only the real,
        finished answer after the search, sent as a single chunk, exactly
        as before live streaming was added for the ordinary case."""
        import wink.blueprints.chat as chat_module
        import wink.config as config

        register(client)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        csrf_token = _chat_csrf_token(client)

        events = [
            FakeEvent("content_block_start", content_block=FakeContentBlock("server_tool_use")),
            FakeEvent("text", text="Let me check on that..."),
            FakeEvent("text", text="The answer is 42."),
        ]
        final_content = [
            FakeContentBlock("server_tool_use", name="web_search", input={"query": "financial aid office"}),
            FakeContentBlock("web_search_tool_result",
                              content=[FakeSearchResult("Financial Aid", "https://example.edu/finaid")]),
            FakeContentBlock("text", text="The answer is 42."),
        ]
        monkeypatch.setattr(chat_module, "anthropic_client", FakeAnthropicClient(FakeStream(events, final_content)))

        resp = client.post("/chat", json={"message": "financial aid?"}, headers={"X-CSRFToken": csrf_token})
        chunks = _chunks(resp)
        assert chunks == ["The answer is 42."], f"narration leaked or answer altered: {chunks}"
        assert "Let me check" not in "".join(chunks)

    def test_search_provenance_still_recorded_for_research(self, client, app, monkeypatch):
        import wink.blueprints.chat as chat_module
        import wink.config as config
        from wink.extensions import get_db

        register(client)
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")
        csrf_token = _chat_csrf_token(client)

        events = [
            FakeEvent("content_block_start", content_block=FakeContentBlock("server_tool_use")),
            FakeEvent("text", text="The answer is 42."),
        ]
        final_content = [
            FakeContentBlock("server_tool_use", name="web_search", input={"query": "financial aid office"}),
            FakeContentBlock("web_search_tool_result",
                              content=[FakeSearchResult("Financial Aid", "https://example.edu/finaid")]),
            FakeContentBlock("text", text="The answer is 42."),
        ]
        monkeypatch.setattr(chat_module, "anthropic_client", FakeAnthropicClient(FakeStream(events, final_content)))

        resp = client.post("/chat", json={"message": "financial aid?"}, headers={"X-CSRFToken": csrf_token})
        list(resp.response)  # drain the generator so the request finishes and logs

        with app.app_context():
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT retrieved_context FROM answer_logs ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            cur.close()
        assert "[web search query] financial aid office" in row["retrieved_context"]
        assert "https://example.edu/finaid" in row["retrieved_context"]
