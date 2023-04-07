import os.path
from unittest.mock import Mock, patch

from mkdocs.config import Config
from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page
import mkdocs.utils
import pytest
from requests import Response

from htmlproofer.plugin import HtmlProoferPlugin


@pytest.fixture
def plugin():
    plugin = HtmlProoferPlugin()
    plugin.load_config({})
    return plugin


@pytest.fixture
def empty_files():
    return {}


@pytest.fixture(autouse=True)
def mock_requests():
    with patch('requests.Session.get') as mock_head:
        mock_head.side_effect = Exception("don't make network requests from tests")
        yield mock_head


@pytest.mark.parametrize(
    'raise_error_after_finish_template', (False, True)
)
@pytest.mark.parametrize(
    'invalid_links_template', (False, True)
)
def test_on_post_build(raise_error_after_finish_template, invalid_links_template):
    plugin = HtmlProoferPlugin()
    plugin.load_config({
        'raise_error_after_finish': raise_error_after_finish_template,
    })

    plugin.invalid_links = invalid_links_template
    config = Mock(spec=Config)

    if invalid_links_template and raise_error_after_finish_template:
        with pytest.raises(PluginError):
            plugin.on_post_build(config)
    else:
        plugin.on_post_build(config)


@pytest.mark.parametrize(
    'validate_rendered_template', (False, True)
)
@pytest.mark.parametrize(
    'raise_error_template', (False, True)
)
@pytest.mark.parametrize(
    'raise_error_after_finish_template', (False, True)
)
def test_on_post_page(
        empty_files,
        mock_requests,
        validate_rendered_template,
        raise_error_template,
        raise_error_after_finish_template
):
    plugin = HtmlProoferPlugin()
    plugin.load_config({
        'validate_rendered_template': validate_rendered_template,
        'raise_error': raise_error_template,
        'raise_error_after_finish': raise_error_after_finish_template,
    })

    # Always raise a 500 error
    mock_requests.side_effect = [Mock(spec=Response, status_code=500)]
    link_to_500 = '<a href="https://google.com"><a/>'

    plugin.files = empty_files
    page = Mock(
        spec=Page,
        file=Mock(spec=File, src_path='blah.md'),
        content='' if validate_rendered_template else link_to_500
    )
    config = Mock(spec=Config, data={'use_directory_urls': False})

    if raise_error_template:
        with pytest.raises(PluginError):
            plugin.on_post_page(link_to_500 if validate_rendered_template else '', page, config)
    else:
        plugin.on_post_page(link_to_500 if validate_rendered_template else '', page, config)
        assert plugin.invalid_links == raise_error_after_finish_template


def test_on_post_page__plugin_disabled():
    plugin = HtmlProoferPlugin()
    plugin.load_config({
        'enabled': False,
        'raise_error': True,
    })
    plugin.on_post_page('<a href="https://google.com"><a/>', Mock(spec=Page), Mock(spec=Config))


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


@pytest.mark.parametrize(
    'validate_external', (True, False)
)
def test_get_url_status(validate_external: bool):
    plugin = HtmlProoferPlugin()
    plugin.load_config({'validate_external_urls': validate_external})

    def get_url():
        return plugin.get_url_status('https://google.com', 'src/path.md', set(), empty_files, False)

    if validate_external:
        with pytest.raises(Exception):
            get_url()
    else:
        assert get_url() == 0


@pytest.mark.parametrize(
    'markdown, anchor, expected', [
        ('git status', 'git-status', False),
        ('## git status', 'git-status', True),
        ('## refer to this [![image](image-link)]', 'refer-to-this', True),
        ('## git add [$changed-files]', 'git-add-changed-files', True),
        ('''## Delete ![][delete_icon]
[delete_icon]: ./delete.svg''', 'delete', True),
        # attr_list extension tests
        (r'## Heading {.customclass}', 'heading', True),
        (r'## Heading {#customanchor}', 'customanchor', True),
        (r'## Heading {: #customanchor}', 'customanchor', True),
        (r'## Heading {.customclass #customanchor}', 'customanchor', True),
        (r'## refer to this ![image](image-link){#imageanchorheading}', 'imageanchorheading', True),
        # test faulty image in heading syntax
        (r'## refer to this ![image](image-link){.customclass}', 'refer-to-this-imageimage-link', True),

        (r'## refer to this [![image](image-link){#imageanchorheading}]', 'imageanchorheading', True),
        (
                r'see image ![image](image-link){#imageanchor1} see image 2 ![image](image-link){#imageanchor2}',
                'imageanchor1',
                True
        ),
        (
                r'see image ![image](image-link){#imageanchor1} see image 2 ![image](image-link){#imageanchor2}',
                'imageanchor2',
                True
        ),
        (r'paragraph text\n{#paragraphanchor}', 'paragraphanchor', True),
        (r'paragraph text\n{#paragraphanchor test', 'paragraphanchor', False),
    ]
)
def test_contains_anchor(plugin, markdown, anchor, expected):
    assert plugin.contains_anchor(markdown, anchor) == expected


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
        'mailto:toto@toto.com',
        'steam://application',
        'file://file',
    ),
)
def test_get_url_status__external(plugin, empty_files, url):
    src_path = 'src/path.md'
    scheme = url.split(":")[0]
    expected_status = 200

    with patch.object(HtmlProoferPlugin, "get_external_url") as mock_get_ext_url:
        mock_get_ext_url.return_value = expected_status
        status = plugin.get_url_status(url, src_path, set(), empty_files, False)

    mock_get_ext_url.assert_called_once_with(url, scheme, src_path)
    assert status == expected_status


@pytest.mark.parametrize("scheme", ('http', 'https'))
def test_get_external_url__web_scheme(scheme):
    src_path = 'src/path.md'
    url = f"{scheme}://path.html"
    expected_status = 200

    with patch.object(HtmlProoferPlugin, "resolve_web_scheme") as mock_resolve_web_scheme:
        mock_resolve_web_scheme.return_value = expected_status
        plugin = HtmlProoferPlugin()
        plugin.load_config({})

        status = plugin.get_external_url(url, scheme, src_path)

    mock_resolve_web_scheme.assert_called_once_with(plugin, url)
    assert status == expected_status


@pytest.mark.parametrize("scheme", ('mailto', 'file', 'steam', 'abc'))
def test_get_external_url__unknown_scheme(scheme):
    src_path = 'src/path.md'
    url = f"{scheme}://path.html"
    expected_status = 0

    with patch.object(HtmlProoferPlugin, "resolve_web_scheme") as mock_resolve_web_scheme:
        mock_resolve_web_scheme.return_value = expected_status
        plugin = HtmlProoferPlugin()
        plugin.load_config({})

        with patch.object(mkdocs.utils.log, "info") as mock_log_info:
            status = plugin.get_external_url(url, scheme, src_path)

    mock_log_info.assert_called_once()
    mock_resolve_web_scheme.assert_not_called()
    assert status == expected_status


def test_get_url_status__local_page(plugin):
    index_page = Mock(spec=Page, markdown='# Heading\nContent')
    page1_page = Mock(spec=Page, markdown='# Page One\n## Sub Heading\nContent')
    files = {os.path.normpath(file.url): file for file in Files([
        Mock(spec=File, src_path='index.md', dest_path='index.html', url='index.html', page=index_page),
        Mock(spec=File, src_path='page1.md', dest_path='page1.html', url='page1.html', page=page1_page),
    ])}

    assert plugin.get_url_status('index.html', 'page1.md', set(), files, False) == 0
    assert plugin.get_url_status('index.html#heading', 'page1.md', set(), files, False) == 0
    assert plugin.get_url_status('index.html#bad-heading', 'page1.md', set(), files, False) == 404

    assert plugin.get_url_status('page1.html', 'page1.md', set(), files, False) == 0
    assert plugin.get_url_status('page1.html#sub-heading', 'page1.md', set(), files, False) == 0
    assert plugin.get_url_status('page1.html#heading', 'page1.md', set(), files, False) == 404

    assert plugin.get_url_status('page2.html', 'page1.md', set(), files, False) == 404
    assert plugin.get_url_status('page2.html#heading', 'page1.md', set(), files, False) == 404


def test_get_url_status__non_markdown_page(plugin):
    index_page = Mock(spec=Page, markdown='# Heading\nContent')
    files = {os.path.normpath(file.url): file for file in Files([
        Mock(spec=File, src_path='index.md', dest_path='index.html', url='index.html', page=index_page),
        Mock(spec=File, src_path='drawing.svg', dest_path='drawing.svg', url='drawing.svg', page=None),
    ])}

    assert plugin.get_url_status('drawing.svg', 'index.md', set(), files, False) == 0
    assert plugin.get_url_status('/drawing.svg', 'index.md', set(), files, False) == 0
    assert plugin.get_url_status('not-existing.svg', 'index.md', set(), files, False) == 404


def test_get_url_status__local_page_nested(plugin):
    index_page = Mock(spec=Page, markdown='# Heading\nContent')
    nested1_page = Mock(spec=Page, markdown='# Nested\n## Nested One\nContent')
    nested1_sibling_page = Mock(spec=Page, markdown='# Nested Sibling')
    nested2_page = Mock(spec=Page, markdown='# Nested\n## Nested Two\nContent')
    nested2_sibling_page = Mock(spec=Page, markdown='# Nested Sibling')
    files = {os.path.normpath(file.url): file for file in Files([
        Mock(spec=File, src_path='index.md', dest_path='index.html', url='index.html', page=index_page),
        Mock(
            spec=File,
            src_path='foo/bar/nested.md',
            dest_path='foo/bar/nested.html',
            url='foo/bar/nested.html',
            page=nested1_page
        ),
        Mock(
            spec=File,
            src_path='foo/bar/sibling.md',
            dest_path='foo/bar/sibling.html',
            url='foo/bar/sibling.html',
            page=nested1_sibling_page
        ),
        Mock(
            spec=File,
            src_path='foo/baz/nested.md',
            dest_path='foo/baz/nested.html',
            url='foo/baz/nested.html',
            page=nested2_page
        ),
        Mock(
            spec=File,
            src_path='foo/baz/sibling.md',
            dest_path='foo/baz/sibling.html',
            url='foo/baz/sibling.html',
            page=nested2_sibling_page
        ),
    ])}

    assert plugin.get_url_status('nested.html#nested-one', 'foo/bar/sibling.md', set(), files, False) == 0
    assert plugin.get_url_status('nested.html#nested-two', 'foo/bar/sibling.md', set(), files, False) == 404

    assert plugin.get_url_status('nested.html#nested-two', 'foo/baz/sibling.md', set(), files, False) == 0
    assert plugin.get_url_status('nested.html#nested-one', 'foo/baz/sibling.md', set(), files, False) == 404

    assert plugin.get_url_status('foo/bar/nested.html#nested-one', 'index.md', set(), files, False) == 0
    assert plugin.get_url_status('foo/baz/nested.html#nested-two', 'index.md', set(), files, False) == 0

    assert plugin.get_url_status('/index.html', 'foo/baz/sibling.md', set(), files, False) == 0
