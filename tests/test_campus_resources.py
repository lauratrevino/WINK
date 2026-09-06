"""Covers services/campus_resources.py: the cache read path, the upsert
being idempotent, and refresh_resource()'s handling of a real Anthropic
response — including the specific empty-text failure mode found in
production (web_search ran, API call succeeded, but the model never
emitted a text block before hitting max_tokens)."""
import json


class FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, response):
        self._response = response

    def create(self, **kwargs):
        return self._response


class FakeAnthropicClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


class TestRefreshResource:
    def test_successful_lookup_caches_contact_info(self, app, monkeypatch):
        import wink.services.campus_resources as cr

        payload = json.dumps({
            "found": True,
            "contact_info": "Phone: 555-1234 | Email: finaid@example.edu",
            "source_urls": ["https://example.edu/finaid"],
        })
        fake = FakeAnthropicClient(FakeResponse([FakeTextBlock(payload)]))
        monkeypatch.setattr(cr, "anthropic_client", fake)

        with app.app_context():
            ok = cr.refresh_resource("Test University", "financial_aid", "Financial Aid Office")
            assert ok is True
            block = cr.get_resources_context_block("Test University")
            assert "Financial Aid Office" in block
            assert "555-1234" in block

    def test_empty_text_response_fails_gracefully_not_crashing(self, app, monkeypatch):
        """The exact production failure: web_search ran (200 OK), but the
        model hit max_tokens before emitting any text block at all — used
        to surface only as an opaque json.JSONDecodeError. Must return
        False, log a clear reason, and never raise."""
        import wink.services.campus_resources as cr

        fake = FakeAnthropicClient(FakeResponse([], stop_reason="max_tokens"))
        monkeypatch.setattr(cr, "anthropic_client", fake)

        with app.app_context():
            ok = cr.refresh_resource("Test University", "financial_aid", "Financial Aid Office")
            assert ok is False

    def test_empty_code_fence_fails_gracefully_not_crashing(self, app, monkeypatch):
        """A second, subtler version of the same underlying failure: the
        model emits an opening/closing ```json fence with nothing real
        inside it (still likely a max_tokens cutoff, just after the fence
        marker instead of before any text at all). This passes the
        not-empty check on the RAW text but must not reach json.loads on
        an empty string after fence-stripping."""
        import wink.services.campus_resources as cr

        fake = FakeAnthropicClient(FakeResponse([FakeTextBlock("```json```")], stop_reason="max_tokens"))
        monkeypatch.setattr(cr, "anthropic_client", fake)

        with app.app_context():
            ok = cr.refresh_resource("Test University", "financial_aid", "Financial Aid Office")
            assert ok is False

    def test_malformed_non_json_text_fails_gracefully_not_crashing(self, app, monkeypatch):
        """Any other genuinely malformed (non-empty, non-fence) response
        text should also fail cleanly rather than raising."""
        import wink.services.campus_resources as cr

        fake = FakeAnthropicClient(FakeResponse([FakeTextBlock("Sorry, I couldn't find that.")]))
        monkeypatch.setattr(cr, "anthropic_client", fake)

        with app.app_context():
            ok = cr.refresh_resource("Test University", "financial_aid", "Financial Aid Office")
            assert ok is False

    def test_json_wrapped_in_markdown_fence_still_parses(self, app, monkeypatch):
        import wink.services.campus_resources as cr

        payload = "```json\n" + json.dumps({
            "found": True, "contact_info": "Email: advising@example.edu", "source_urls": [],
        }) + "\n```"
        fake = FakeAnthropicClient(FakeResponse([FakeTextBlock(payload)]))
        monkeypatch.setattr(cr, "anthropic_client", fake)

        with app.app_context():
            ok = cr.refresh_resource("Test University", "advising", "Academic Advising")
            assert ok is True

    def test_not_found_does_not_cache_anything(self, app, monkeypatch):
        import wink.services.campus_resources as cr

        payload = json.dumps({"found": False})
        fake = FakeAnthropicClient(FakeResponse([FakeTextBlock(payload)]))
        monkeypatch.setattr(cr, "anthropic_client", fake)

        with app.app_context():
            ok = cr.refresh_resource("Test University", "career_center", "Career Center")
            assert ok is False
            assert cr.get_resources_context_block("Test University") == ""


class TestResourceCache:
    def test_upsert_is_idempotent_and_overwrites(self, app):
        import wink.services.campus_resources as cr

        with app.app_context():
            cr._upsert_resource("Test University", "counseling", "Counseling Services",
                                 "Phone: 111-1111", ["https://example.edu"])
            cr._upsert_resource("Test University", "counseling", "Counseling Services",
                                 "Phone: 222-2222", ["https://example.edu"])
            block = cr.get_resources_context_block("Test University")
            assert block.count("Counseling Services") == 1
            assert "222-2222" in block
            assert "111-1111" not in block

    def test_no_cache_returns_empty_string_not_none_or_error(self, app):
        import wink.services.campus_resources as cr

        with app.app_context():
            block = cr.get_resources_context_block("A University With No Cache Entries")
            assert block == ""
