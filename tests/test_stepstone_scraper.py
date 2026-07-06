from app.scrapers.stepstone_scraper import _search_url, _slugify_segment


def test_slugify_keeps_german_job_title_letters():
    assert _slugify_segment("Softwareentwickler") == "softwareentwickler"
    assert "wicker" not in _slugify_segment("Softwareentwickler")


def test_slugify_german_city_and_umlauts():
    assert _slugify_segment("München") == "munchen"
    assert _slugify_segment("Groß-Umstadt") == "gross-umstadt"


def test_search_url_joins_query_and_location():
    u = _search_url("Data Analyst", "Berlin")
    assert u == "https://www.stepstone.de/work/data-analyst-in-berlin"
