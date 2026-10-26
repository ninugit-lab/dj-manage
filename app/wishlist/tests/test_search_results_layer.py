import pytest
from html.parser import HTMLParser
from datetime import date
from wishlist.models import Event, EventStatus


class SearchSectionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.search_section = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'section':
            self.sections.append(attrs)
        if attrs.get('id') == 'search-results' and self.sections:
            self.search_section = self.sections[-1]

    def handle_endtag(self, tag):
        if tag == 'section' and self.sections:
            self.sections.pop()


@pytest.mark.django_db
def test_song_search_dropdown_stacks_above_wishlist(client):
    Event.objects.create(name='Live Test', date=date(2026, 11, 28),
                         status=EventStatus.CONFIRMED, is_active=True)
    response = client.get('/')
    assert response.status_code == 200
    parser = SearchSectionParser()
    parser.feed(response.content.decode())
    assert parser.search_section is not None
    assert parser.search_section['style'] == 'z-index:20'
