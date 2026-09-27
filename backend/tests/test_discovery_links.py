"""Issuer-to-asset provenance uses fetched, bounded links, never locator claims."""
from backend.app.research.discovery import FetchedSource, _PageTextParser, _public_page_links


def test_visible_links_are_collected_without_hidden_markup_and_normalized():
    parser = _PageTextParser()
    parser.feed('''<html><body><h1>Quarterly results</h1>
        <a href="/files/slides.pdf#page=2">Slides</a>
        <a href="https://cdn.example.com/supplement.pdf">Supplement</a>
        <a href="javascript:alert(1)">Ignore</a>
        <script><a href="https://evil.example.com/script">Hidden</a></script>
        <template><a href="https://evil.example.com/template">Hidden</a></template>
        <a href="https://user:pass@example.com/private">Ignore credentials</a>
        <a href="http://example.com/legacy">Ignore insecure</a>
        <a href="/files/slides.pdf">Repeated</a>
        <a href="https://[invalid">Malformed</a>
        </body></html>''')
    assert _public_page_links(parser.links, "https://investor.example.com/results/") == (
        "https://investor.example.com/files/slides.pdf",
        "https://cdn.example.com/supplement.pdf",
    )
    assert "Hidden" not in "".join(parser.parts)


def test_link_collection_is_bounded_and_fetched_source_stays_positional_compatible():
    parser = _PageTextParser()
    parser.feed("".join(f'<a href="/item-{i}.pdf">Item</a>' for i in range(600)))
    assert len(parser.links) == 500
    assert len(_public_page_links(parser.links, "https://issuer.example.com")) == 500
    original = FetchedSource("https://example.com", "https://example.com", "text", "title", "2026-09-25", None, None, None)
    assert original.links == ()


def test_embedded_document_links_use_the_same_bounded_https_validation():
    parser = _PageTextParser()
    parser.feed('<iframe src="/documents/earnings.pdf"></iframe><embed src="https://cdn.example.com/slides.pdf"><object data="javascript:bad"></object>')
    assert _public_page_links(parser.links, "https://issuer.example.com/events/") == (
        "https://issuer.example.com/documents/earnings.pdf", "https://cdn.example.com/slides.pdf",
    )
