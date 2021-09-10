from unittest.mock import Mock, patch

from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page
import pytest

from htmlproofer.plugin import HtmlProoferPlugin


@pytest.fixture
def plugin():
    return HtmlProoferPlugin()


@pytest.fixture
def empty_files():
    return Files([])


@pytest.fixture(autouse=True)
def mock_requests():
    with patch('requests.Session.head') as mock_head:
        mock_head.side_effect = Exception("don't make network requests from tests")
        yield


@pytest.mark.parametrize(
    'url',
    (
        'http://localhost/',
        'https://127.0.0.1/something',
        'http://app_server/#foo',
    ),
)
def test_get_url_status__ignore_local_servers(plugin, empty_files, url):
    assert plugin.get_url_status(url, 'src/path.md', set(), empty_files, False) == 0


def test_get_url_status__dont_validate_external(plugin):
    assert plugin.get_url_status('https://google.com', 'src/path.md', set(), empty_files, False) == 0


def test_get_url_status__same_page_anchor(plugin, empty_files):
    assert plugin.get_url_status('#ref', 'src/path.md', {'ref'}, empty_files, False) == 0
    assert plugin.get_url_status('##ref', 'src/path.md', {'ref'}, empty_files, False) == 404
    assert plugin.get_url_status('#ref', 'src/path.md', set(), empty_files, False) == 404


@pytest.mark.parametrize(
    'url',
    (
        'https://extwebsite.com',
        'http://extwebsite.com',
        'https://website.net/path#anchor',
    ),
)
def test_get_url_status__external(plugin, empty_files, url):
    with patch.object(HtmlProoferPlugin, "get_external_url") as mock_get_ext_url:
        mock_get_ext_url.return_value = 200
        assert plugin.get_url_status(url, 'src/path.md', set(), empty_files, False) == 200
    mock_get_ext_url.assert_called_once_with(url)


def test_get_url_status__local_page(plugin):
    index_page = Mock(spec=Page, markdown='# Heading\nContent')
    page1_page = Mock(spec=Page, markdown='# Page One\n## Sub Heading\nContent')
    files = Files([
        Mock(spec=File, src_path='index.md', dest_path='index.html', url='index.html', page=index_page),
        Mock(spec=File, src_path='page1.md', dest_path='page1.html', url='page1.html', page=page1_page),
    ])

    assert plugin.get_url_status('index.html#heading', 'page1.md', set(), files, False) == 0
    assert plugin.get_url_status('index.html#bad-heading', 'page1.md', set(), files, False) == 404

    assert plugin.get_url_status('page1.html#sub-heading', 'page1.md', set(), files, False) == 0
    assert plugin.get_url_status('page1.html#heading', 'page1.md', set(), files, False) == 404

    assert plugin.get_url_status('page2.html#heading', 'page1.md', set(), files, False) == 404


def test_get_url_status__local_page_nested(plugin):
    index_page = Mock(spec=Page, markdown='# Heading\nContent')
    nested1_page = Mock(spec=Page, markdown='# Nested\n## Nested One\nContent')
    nested1_sibling_page = Mock(spec=Page, markdown='# Nested Sibling')
    nested2_page = Mock(spec=Page, markdown='# Nested\n## Nested Two\nContent')
    nested2_sibling_page = Mock(spec=Page, markdown='# Nested Sibling')
    files = Files([
        Mock(spec=File, src_path='index.md', dest_path='index.html', url='index.html', page=index_page),
        Mock(spec=File, src_path='foo/bar/nested.md', dest_path='foo/bar/nested.html', url='foo/bar/nested.html', page=nested1_page),
        Mock(spec=File, src_path='foo/bar/sibling.md', dest_path='foo/bar/sibling.html', url='foo/bar/sibling.html', page=nested1_sibling_page),
        Mock(spec=File, src_path='foo/baz/nested.md', dest_path='foo/baz/nested.html', url='foo/baz/nested.html', page=nested2_page),
        Mock(spec=File, src_path='foo/baz/sibling.md', dest_path='foo/baz/sibling.html', url='foo/baz/sibling.html', page=nested2_sibling_page),
    ])

    assert plugin.get_url_status('nested.html#nested-one', 'foo/bar/sibling.md', set(), files, False) == 0
    assert plugin.get_url_status('nested.html#nested-two', 'foo/bar/sibling.md', set(), files, False) == 404

    assert plugin.get_url_status('nested.html#nested-two', 'foo/baz/sibling.md', set(), files, False) == 0
    assert plugin.get_url_status('nested.html#nested-one', 'foo/baz/sibling.md', set(), files, False) == 404

    assert plugin.get_url_status('foo/bar/nested.html#nested-one', 'index.md', set(), files, False) == 0
    assert plugin.get_url_status('foo/baz/nested.html#nested-two', 'index.md', set(), files, False) == 0
