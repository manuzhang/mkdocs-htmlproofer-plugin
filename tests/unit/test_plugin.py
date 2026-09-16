import os.path
from unittest.mock import Mock, patch

from mkdocs.config import Config
from mkdocs.exceptions import PluginError
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page
import mkdocs.utils
import pytest
import requests
from requests import Response

import htmlproofer.plugin
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
    link_to_500 = '<a href="https://google.com"><a/>'
    iter_content = Mock()
    iter_content.side_effect = link_to_500
    mock_requests.side_effect = [Mock(spec=Response, status_code=500, iter_content=iter_content)]

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


def test_on_post_page__img():
    plugin = HtmlProoferPlugin()
    plugin.load_config({
        'validate_rendered_template': True,
        'raise_error': True,
    })
    page = Mock(
        spec=Page,
        file=Mock(spec=File, src_path='blah.md'),
        content='',
    )
    with pytest.raises(PluginError):
        plugin.on_post_page('<img src="not-existing.png" />', page, Mock(spec=Config))


def test_on_post_page__img_without_src():
    plugin = HtmlProoferPlugin()
    plugin.load_config({
        'validate_rendered_template': True,
        'raise_error': True,
    })
    page = Mock(
        spec=Page,
        file=Mock(spec=File, src_path='blah.md'),
        content='',
    )
    plugin.on_post_page('<img data-src="lazy-loaded.png" />', page, Mock(spec=Config))


@pytest.mark.parametrize(
    'url',
    (
        'http://localhost/',
        'https://127.0.0.1/something',
        'http://app_server/#foo',
    ),
)
def test_get_url_status__ignore_local_servers(plugin, empty_files, url):
    assert plugin.get_url_status(url, 'src/path.md', set(), empty_files) == 0


@pytest.mark.parametrize(
    'validate_external', (True, False)
)
def test_get_url_status(empty_files, validate_external: bool):
    plugin = HtmlProoferPlugin()
    plugin.load_config({'validate_external_urls': validate_external})

    def get_url():
        return plugin.get_url_status('https://google.com', 'src/path.md', set(), empty_files)

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
        (r'## {#customanchor} Heading', 'customanchor-heading', True),
        (r'## refer to this ![image](image-link){#imageanchorheading}', 'imageanchorheading', True),
        (r'## refer to this ![image](image-link){.customclass}', 'refer-to-this', True),
        ('# Heading [![alt][img]][target]\n\n[img]: i.png\n[target]: t.html', 'heading', True),
        # An undefined reference renders literally, so it stays part of the slug
        ('# Heading [text][undefined]', 'heading-textundefined', True),
        ('# Heading [text][undefined]', 'heading-text', False),
        ('# Heading [text][ref]\n\n[ref]: t.html', 'heading-text', True),
        # Markdown within a code span is literal text
        ('# Example `[![alt](src)](href)`', 'example-altsrchref', True),
        ('# Example `{#codeid}` text', 'example-codeid-text', True),
        ('## `code`{#codeid} text', 'codeid', True),
        ('## `code`{#codeid} text', 'code-text', True),
        # An attribute list applies to an inline element, not to literal punctuation
        ('# Heading (text){#id}', 'heading-textid', True),
        # Indented content of a list or an admonition is Markdown rather than code
        ('!!! note\n\n    ## Inner heading\n', 'inner-heading', True),
        ('- item\n\n    ## Listed heading\n', 'listed-heading', True),
        # A linked image, as in the README's own heading
        ('# mkdocs-htmlproofer-plugin [![PyPI - Version](https://img.shields.io/pypi/v/x.svg)]'
         '(https://pypi.org/project/x)', 'mkdocs-htmlproofer-plugin', True),
        # An attribute list only applies where attr_list would apply it
        (r'## Heading {.customclass}', 'heading-customclass', False),
        (r'## Heading {.a} and {.b}', 'heading-a-and-b', True),
        (r'## Heading {#a} {#b}', 'heading-a-b', True),
        # Only a link's text ends up in the heading
        ('# Heading [text](href)', 'heading-text', True),
        # Every attribute list directly following an inline element applies
        (r'## *one*{.red} and *two*{.blue}', 'one-and-two', True),
        ('## <https://example.com>{#target}', 'target', True),
        ('## <https://example.com>{#target}', 'httpsexamplecom', True),
        # An id is a whole token, so this one sets a title rather than an id
        (r'## Heading {title="#tooltip"}', 'heading', True),
        (r'## Heading {title="#tooltip"}', 'tooltip', False),
        (r'## Heading {title="#tooltip" #real}', 'real', True),
        # A closing sequence of #'s is stripped before the attribute list is applied
        (r'## Heading {.class} ##', 'heading', True),
        (r'## Heading {#anchor} ##', 'anchor', True),
        (r'## Heading{#nospace} ##', 'headingnospace', True),
        (r'## Heading ##', 'heading', True),
        (r'## Heading{#nospace}', 'headingnospace', True),
        ('Setext {#setextid}\n---', 'setextid', True),

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
        ('paragraph text\n{#paragraphanchor}', 'paragraphanchor', True),
        (r'paragraph text\n{#paragraphanchor test', 'paragraphanchor', False),
        ('Paragraph text\n  {#paragraphanchor}', 'paragraphanchor', True),
        ('| A | B |\n|---|---|\n| Cell {#cellanchor} | Other |', 'cellanchor', True),
        # Each table cell is its own element, so an attribute list may end each of them
        ('| A | B |\n|---|---|\n| A {#a} | B {#b} |', 'a', True),
        ('| A | B |\n|---|---|\n| A {#a} | B {#b} |', 'b', True),
        # An id written as an attribute rather than as the `#id` shorthand
        ('## Heading {id=foo}', 'foo', True),
        ('## Heading {id="foo"}', 'foo', True),
        # A label may hold balanced brackets
        ('# [outer [inner]](target)', 'outer-inner', True),
        # The last id in an attribute list wins
        ('## Heading {#first #second}', 'second', True),
        # A reference label matches however its whitespace is written
        ('# Heading [text][a  b]\n\n[a b]: t.html', 'heading-text', True),
        # A definition within a code block doesn't define the reference, in either mode
        ('# Heading [text][ref]\n\n```\n[ref]: t.html\n```', 'heading-textref', True),
        ('# Heading [text][ref]\n\n```\n[ref]: t.html\n```', 'heading-text', False),
        # The toc extension suffixes repeated headings
        ('# Repeat\n\n# Repeat', 'repeat', True),
        ('# Repeat\n\n# Repeat', 'repeat_1', True),
        # An id set by attr_list claims its name before generated slugs, wherever it's set
        ('# Repeat\n\n# Other {#repeat}', 'repeat', True),
        ('# Repeat\n\n# Other {#repeat}', 'repeat_1', True),
        ('Paragraph\n{#repeat}\n\n# Repeat', 'repeat', True),
        ('Paragraph\n{#repeat}\n\n# Repeat', 'repeat_1', True),
        # A heading indented under a nested list is content, not code
        ('- outer\n\n    - inner\n\n        # Deep\n', 'deep', True),
        # Comment syntax within a code span is literal text
        ('# Heading `<!-- c -->` text', 'heading-c-text', True),
        # After an ATX heading, a line of dashes is a thematic break rather than an underline,
        # so the heading isn't counted twice and uniquified
        ('# Heading\n---', 'heading', True),
        ('# Heading\n---', 'heading_1', False),
        # Python-Markdown caps a heading at six levels and renders the rest as text
        ('####### fake', 'fake', True),
        ('###### six', 'six', True),
        # A block quote's content is rendered as Markdown
        ('> # Quoted heading\n', 'quoted-heading', True),
        ('> Paragraph\n> {#quotedid}\n', 'quotedid', True),
        # An image written as a shortcut reference renders when its label is defined
        ('# Heading ![alt]\n\n[alt]: i.png', 'heading', True),
        ('# Heading ![alt]', 'heading-alt', True),
        # A destination may contain balanced parentheses
        ('# Heading ![alt](https://example.com/a_(b).png)', 'heading', True),
        ('# Heading [![alt](https://example.com/a_(b).png)](https://example.com/x_(y))', 'heading', True),
        # An escaped bracket renders literally, so this is neither a link nor an image
        (r'# Heading \[text](href)', 'heading-texthref', True),
        (r'# Heading \[text](href)', 'heading-text', False),
        (r'# Heading \![alt](src)', 'heading-alt', True),
        # Only an ATX heading has a closing sequence of #'s
        ('Title {#id} ##\n---', 'title-id', True),
        # HTML anchor with id attribute
        (r'<a id="myanchor"></a>', 'myanchor', True),
        (r'<a id="myanchor">Link text</a>', 'myanchor', True),
        # HTML anchor with name attribute (legacy)
        (r'<a name="myanchor"></a>', 'myanchor', True),
        (r'<a name="myanchor">Link text</a>', 'myanchor', True),
        # HTML anchor in table cell
        (r'<td rowspan="9"><a name="license"></a>foo bar</td>', 'license', True),
        (r'<td><a id="REGISTER"></a>REGISTER</td>', 'REGISTER', True),
        # Anchor with dots (like REGISTER.FIELD1)
        (r'<a name="REGISTER.FIELD1"></a>FIELD1', 'REGISTER.FIELD1', True),
        # Setext headings
        ('Heading\n=======', 'heading', True),
        ('Sub Heading\n-----------\nContent', 'sub-heading', True),
        ('Sub Heading {#customanchor}\n---', 'customanchor', True),
        ('Paragraph\n\n---', 'paragraph', False),
        # A fenced code block doesn't stop the rest of the page providing anchors
        ('    ```python\n    # comment\n    ```\n# Heading', 'heading', True),
        ('```\nUnclosed fence\n# Heading', 'heading', True),
        # The underline of a Setext heading may be indented by at most three spaces
        ('    Fake\n    ---', 'fake', False),
    ]
)
def test_contains_anchor(plugin, markdown, anchor, expected):
    assert plugin.contains_anchor(markdown, anchor) == expected
    assert plugin.contains_anchor(markdown, anchor, strict_anchors=True) == expected


# Anchors which the rendered page doesn't contain, but which 1.5.0 accepted
STRICT_ONLY_ANCHORS = [
    # attr_list applies an attribute list at the end of a heading, not at its start
    (r'## {#customanchor} Heading', 'customanchor'),
    # An id set by attr_list replaces the generated slug
    (r'## Heading {#customanchor}', 'heading'),
    # attr_list applies neither of two attribute lists ending a heading
    (r'## Heading {#a} {#b}', 'b'),
    # An attribute list needs a space before it, or to follow an inline element
    (r'## Heading{#nospace}', 'nospace'),
    ('Text {#literal} more text', 'literal'),
    # Code blocks render no headings or anchors
    ('```\nFake\n---\n```', 'fake'),
    ('```bash\n# comment\n```', 'comment'),
    ('~~~\n<a id="fake"></a>\n~~~', 'fake'),
    ('```\n~~~\n# Mixed fences\n```', 'mixed-fences'),
    ('````markdown\n```\n# Nested\n```\n````', 'nested'),
    ('    ```python\n    # comment\n    ```\n# Heading', 'comment'),
    ('    Fake\n---', 'fake'),
    # The closing #'s of a Setext heading are content, so its attribute list isn't trailing
    ('Title {#id} ##\n---', 'id'),
    # An indented code block renders no heading
    ('Intro\n\n    # comment\n\nOutro', 'comment'),
    # Literal punctuation doesn't make an attribute list apply to an inline element
    ('# Heading (text){#id}', 'id'),
    # An attribute list within a code span is literal text
    ('# Example `{#codeid}` text', 'codeid'),
    # Pipes only end a cell within a table, and a standalone list must occupy the whole line
    ('| A {#fake} | B |', 'fake'),
    ('{#fake} and more text', 'fake'),
    # Escaping the delimiter leaves emphasis, and an undefined reference a link, as literal text
    (r'# Heading \*not em\*{#fake}', 'fake'),
    ('# Heading [text][missing]{#fake}', 'fake'),
    # A code block nested in a list item renders no heading
    ('- item\n\n        # fake\n', 'fake'),
    # A tab indents a code block just as four spaces do
    ('Intro\n\n\t# fake\n\nOutro', 'fake'),
    # attr_list applies a trailing list to a heading or a table cell, but not to a paragraph,
    # a list item or a definition, where it stays literal text
    ('Paragraph {#inline}', 'inline'),
    ('- item {#listitem}', 'listitem'),
    ('Term\n\n:   def {#defitem}', 'defitem'),
    # An anchor within an HTML comment isn't rendered
    ('Text\n\n<!-- <a id="fake"></a> -->\n', 'fake'),
    # Code indented four columns past a list item's content is a code block
    ('- outer\n\n        # Eight\n', 'eight'),
    # Only the last id of an attribute list is applied
    ('## Heading {#first #second}', 'first'),
]


@pytest.mark.parametrize('markdown, anchor', STRICT_ONLY_ANCHORS)
def test_contains_anchor__strict_anchors(plugin, markdown, anchor):
    assert plugin.contains_anchor(markdown, anchor, strict_anchors=True) is False


@pytest.mark.parametrize('markdown, anchor', STRICT_ONLY_ANCHORS)
def test_contains_anchor__accepted_without_strict_anchors(plugin, markdown, anchor):
    # Without the option, these keep passing, so upgrading doesn't fail a build which passed before
    assert plugin.contains_anchor(markdown, anchor) is True


@pytest.mark.parametrize(
    'markdown, anchor, expected', [
        # Without the extension an attribute list is literal text, which the slug includes
        ('## Heading {#id}', 'heading-id', True),
        ('## Heading {#id}', 'id', False),
        ('## Heading {.cls}', 'heading-cls', True),
        ('## Heading {.cls}', 'heading', False),
    ]
)
def test_contains_anchor__without_attr_list(plugin, markdown, anchor, expected):
    assert plugin.contains_anchor(markdown, anchor, strict_anchors=True, attr_list=False) == expected


@pytest.mark.parametrize(
    'markdown, containers, expected', [
        # Each marker needs the extension which renders it, or its content is an indented code block
        ('!!! note\n\n    ## Inner heading\n', frozenset({'!!!'}), True),
        ('!!! note\n\n    ## Inner heading\n', frozenset({'???'}), False),
        ('!!! note\n\n    ## Inner heading\n', frozenset(), False),
        ('??? note\n\n    ## Inner heading\n', frozenset({'???'}), True),
        ('??? note\n\n    ## Inner heading\n', frozenset({'!!!'}), False),
    ]
)
def test_contains_anchor__container_markers(plugin, markdown, containers, expected):
    assert plugin.contains_anchor(markdown, 'inner-heading', strict_anchors=True,
                                  containers=containers) == expected


def test_contains_anchor__without_tables(plugin):
    # Without the extension a table-shaped line is an ordinary paragraph, whose pipes end no cell
    table = '| A | B |\n|---|---|\n| A {#a} | B {#b} |'

    assert plugin.contains_anchor(table, 'a', strict_anchors=True) is True
    assert plugin.contains_anchor(table, 'a', strict_anchors=True, tables=False) is False


@pytest.mark.parametrize(
    'separator, anchor, expected', [
        ('-', 'sub-heading', True),
        ('-', 'sub_heading', False),
        ('_', 'sub_heading', True),
        ('_', 'sub-heading', False),
    ]
)
def test_contains_anchor__toc_separator(plugin, separator, anchor, expected):
    assert plugin.contains_anchor('## Sub Heading', anchor, strict_anchors=True,
                                  separator=separator) == expected


@pytest.mark.parametrize(
    'markdown_extensions, attr_list, tables, containers', [
        (['attr_list', 'tables', 'admonition'], True, True, {'!!!'}),
        (['markdown.extensions.attr_list'], True, False, set()),
        (['tables', 'pymdownx.details'], False, True, {'???'}),
        (['admonition', 'pymdownx.details'], False, False, {'!!!', '???'}),
        ([], False, False, set()),
    ]
)
def test_on_config__extensions(plugin, markdown_extensions, attr_list, tables, containers):
    config = Mock(spec=Config)
    config.get.side_effect = lambda key, default=None: {
        'markdown_extensions': markdown_extensions, 'mdx_configs': {}
    }.get(key, default)

    plugin.on_config(config)

    assert plugin.attr_list == attr_list
    assert plugin.tables == tables
    assert plugin.containers == containers


@pytest.mark.parametrize(
    'mdx_configs, expected', [
        ({}, '-'),
        ({'toc': {}}, '-'),
        ({'toc': {'separator': '_'}}, '_'),
        # MkDocs keys the configuration by the name the extension was enabled under
        ({'markdown.extensions.toc': {'separator': '_'}}, '_'),
        ({'toc': None}, '-'),
    ]
)
def test_on_config__toc_separator(plugin, mdx_configs, expected):
    config = Mock(spec=Config)
    config.get.side_effect = lambda key, default=None: {
        'markdown_extensions': ['toc'], 'mdx_configs': mdx_configs
    }.get(key, default)

    plugin.on_config(config)

    assert plugin.separator == expected


@pytest.mark.parametrize('strict_anchors', (False, True))
def test_get_url_status__strict_anchors(strict_anchors):
    plugin = HtmlProoferPlugin()
    plugin.load_config({'strict_anchors': strict_anchors})
    # attr_list gives this heading the id `custom`, so `#heading` isn't rendered
    mock_files = Files([
        Mock(spec=File, src_path='index.md', dest_path='index.html', dest_uri='index.html',
             url='index.html', src_uri='index.md', page=Mock(spec=Page, markdown='')),
        Mock(spec=File, src_path='page.md', dest_path='page.html', dest_uri='page.html',
             url='page.html', src_uri='page.md', page=Mock(spec=Page, markdown='# Heading {#custom}')),
    ])
    files = {}
    files.update({os.path.normpath(file.url): file for file in mock_files})
    files.update({file.src_uri: file for file in mock_files})

    assert plugin.get_url_status('page.html#custom', 'index.md', set(), files) == 0
    assert plugin.get_url_status('page.html#heading', 'index.md', set(), files) == (404 if strict_anchors else 0)


def test_get_url_status__same_page_anchor(plugin, empty_files):
    assert plugin.get_url_status('#ref', 'src/path.md', {'ref'}, empty_files) == 0
    assert plugin.get_url_status('##ref', 'src/path.md', {'ref'}, empty_files) == 404
    assert plugin.get_url_status('#ref', 'src/path.md', set(), empty_files) == 404


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
        status = plugin.get_url_status(url, src_path, set(), empty_files)

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
    special_char_page = Mock(spec=Page, markdown='# Heading éèà\n## Sub Heading éèà\nContent')
    mock_files = Files([
        Mock(spec=File, src_path='index.md', dest_path='index.html',
             dest_uri='index.html', url='index.html', src_uri='index.md',
             page=index_page),
        Mock(spec=File, src_path='page1.md', dest_path='page1.html',
             dest_uri='page1.html', url='page1.html', src_uri='page1.md',
             page=page1_page),
        Mock(spec=File, src_path='Dir éèà/éèà.md', dest_path='Dir éèà/éèà.html',
             dest_uri='Dir éèà/éèà.html',
             url='Dir%20%C3%A9%C3%A8%C3%A0/%C3%A9%C3%A8%C3%A0.html',
             src_uri='Dir éèà/éèà.md', page=special_char_page),
        Mock(spec=File, src_path='Dir éèà/page1.md', dest_path='Dir éèà/page1.html',
             dest_uri='Dir éèà/page1.html',
             url='Dir%20%C3%A9%C3%A8%C3%A0/page1.html',
             src_uri='Dir%20%C3%A9%C3%A8%C3%A0/page1.md',
             page=special_char_page),
    ])
    files = {}
    files.update({os.path.normpath(file.url): file for file in mock_files})
    files.update({file.src_uri: file for file in mock_files})

    assert plugin.get_url_status('index.html', 'page1.md', set(), files) == 0
    assert plugin.get_url_status('index.html#heading', 'page1.md', set(), files) == 0
    assert plugin.get_url_status('index.html#bad-heading', 'page1.md', set(), files) == 404

    assert plugin.get_url_status('page1.html', 'page1.md', set(), files) == 0
    assert plugin.get_url_status('page1.html#sub-heading', 'page1.md', set(), files) == 0
    assert plugin.get_url_status('page1.html#heading', 'page1.md', set(), files) == 404

    assert plugin.get_url_status('page2.html', 'page1.md', set(), files) == 404
    assert plugin.get_url_status('page2.html#heading', 'page1.md', set(), files) == 404

    assert plugin.get_url_status(
        'Dir%20%C3%A9%C3%A8%C3%A0/%C3%A9%C3%A8%C3%A0.html#sub-heading-eea',
        'page1.md', set(), files) == 0
    assert plugin.get_url_status(
        '%C3%A9%C3%A8%C3%A0.html#sub-heading-eea',
        'Dir%20%C3%A9%C3%A8%C3%A0/page1.md',
        set(), files) == 0


def test_get_url_status__local_page_with_directory_urls(plugin):
    index_page = Mock(spec=Page, markdown='# Heading\nContent')
    page1_page = Mock(spec=Page, markdown='# Page One\n## Sub Heading\nContent')
    special_char_page = Mock(spec=Page, markdown='# Heading éèà\n## Sub Heading éèà\nContent')
    mock_files = Files([
        Mock(spec=File, src_path='index.md', dest_path='index/index.html',
             dest_uri='index/index.html', url='index/', src_uri='index.md',
             page=index_page),
        Mock(spec=File, src_path='page1.md', dest_path='page1/index.html',
             dest_uri='page1/index.html', url='page1/', src_uri='page1.md',
             page=page1_page),
        Mock(spec=File, src_path='Dir éèà/éèà.md', dest_path='Dir éèà/éèà/index.html',
             dest_uri='Dir éèà/éèà/index.html',
             url='Dir%20%C3%A9%C3%A8%C3%A0/%C3%A9%C3%A8%C3%A0/',
             src_uri='Dir éèà/éèà.md', page=special_char_page),
        Mock(spec=File, src_path='Dir éèà/page1.md', dest_path='Dir éèà/page1/index.html',
             dest_uri='Dir éèà/page1/index.html',
             url='Dir%20%C3%A9%C3%A8%C3%A0/page1/',
             src_uri='Dir%20%C3%A9%C3%A8%C3%A0/page1.md',
             page=special_char_page),
    ])
    files = {}
    files.update({os.path.normpath(file.url): file for file in mock_files})
    files.update({file.src_uri: file for file in mock_files})

    assert plugin.get_url_status('../index/', 'page1.md', set(), files) == 0
    assert plugin.get_url_status('../index/#heading', 'page1.md', set(), files) == 0
    assert plugin.get_url_status('../index/#bad-heading', 'page1.md', set(), files) == 404

    assert plugin.get_url_status('../page1/', 'page1.md', set(), files) == 0
    assert plugin.get_url_status('../page1/#sub-heading', 'page1.md', set(), files) == 0
    assert plugin.get_url_status('../page1/#heading', 'page1.md', set(), files) == 404

    assert plugin.get_url_status('../page2/', 'page1.md', set(), files) == 404
    assert plugin.get_url_status('../page2/#heading', 'page1.md', set(), files) == 404

    assert plugin.get_url_status(
        '../Dir%20%C3%A9%C3%A8%C3%A0/%C3%A9%C3%A8%C3%A0/#sub-heading-eea',
        'page1.md', set(), files) == 0
    assert plugin.get_url_status(
        '../%C3%A9%C3%A8%C3%A0/#sub-heading-eea',
        'Dir%20%C3%A9%C3%A8%C3%A0/page1.md',
        set(), files) == 0


def test_get_url_status__non_markdown_page(plugin):
    index_page = Mock(spec=Page, markdown='# Heading\nContent')
    mock_files = Files([
        Mock(spec=File, src_path='index.md', dest_path='index.html',
             dest_uri='index.html', url='index.html', src_uri='index.md',
             page=index_page),
        Mock(spec=File, src_path='drawing.svg', dest_path='drawing.svg',
             dest_uri='index.html', url='drawing.svg', src_uri='drawing.svg',
             page=None),
        Mock(spec=File, src_path='page.html', dest_path='page.html',
             dest_uri='page.html', url='page.html', src_uri='page.html',
             page=None),
    ])
    files = {}
    files.update({os.path.normpath(file.url): file for file in mock_files})
    files.update({file.src_uri: file for file in mock_files})

    assert plugin.get_url_status('drawing.svg', 'index.md', set(), files) == 0
    assert plugin.get_url_status('/drawing.svg', 'index.md', set(), files) == 0
    assert plugin.get_url_status('not-existing.svg', 'index.md', set(), files) == 404

    assert plugin.get_url_status('page.html', 'index.md', set(), files) == 0
    assert plugin.get_url_status('page.html#heading', 'index.md', set(), files) == 0  # no validation for non-markdown pages


def test_get_url_status__local_page_nested(plugin):
    index_page = Mock(spec=Page, markdown='# Heading\nContent')
    nested1_page = Mock(spec=Page, markdown='# Nested\n## Nested One\nContent')
    nested1_sibling_page = Mock(spec=Page, markdown='# Nested Sibling')
    nested2_page = Mock(spec=Page, markdown='# Nested\n## Nested Two\nContent')
    nested2_sibling_page = Mock(spec=Page, markdown='# Nested Sibling')
    mock_files = Files([
        Mock(
            spec=File,
            src_path='index.md',
            dest_path='index.html',
            dest_uri='index.html',
            url='index.html',
            src_uri='index.md',
            page=index_page),
        Mock(
            spec=File,
            src_path='foo/bar/nested.md',
            dest_path='foo/bar/nested.html',
            dest_uri='foo/bar/nested.html',
            url='foo/bar/nested.html',
            src_uri='foo/bar/nested.md',
            page=nested1_page
        ),
        Mock(
            spec=File,
            src_path='foo/bar/sibling.md',
            dest_path='foo/bar/sibling.html',
            dest_uri='foo/bar/sibling.html',
            url='foo/bar/sibling.html',
            src_uri='foo/bar/sibling.md',
            page=nested1_sibling_page
        ),
        Mock(
            spec=File,
            src_path='foo/baz/nested.md',
            dest_path='foo/baz/nested.html',
            dest_uri='foo/baz/nested.html',
            url='foo/baz/nested.html',
            src_uri='foo/baz/nested.md',
            page=nested2_page
        ),
        Mock(
            spec=File,
            src_path='foo/baz/sibling.md',
            dest_path='foo/baz/sibling.html',
            dest_uri='foo/baz/sibling.html',
            url='foo/baz/sibling.html',
            src_uri='foo/baz/sibling.md',
            page=nested2_sibling_page
        ),
    ])

    files = {}
    files.update({os.path.normpath(file.url): file for file in mock_files})
    files.update({file.src_uri: file for file in mock_files})

    assert plugin.get_url_status('nested.html#nested-one', 'foo/bar/sibling.md', set(), files) == 0
    assert plugin.get_url_status('nested.html#nested-two', 'foo/bar/sibling.md', set(), files) == 404

    assert plugin.get_url_status('nested.html#nested-two', 'foo/baz/sibling.md', set(), files) == 0
    assert plugin.get_url_status('nested.html#nested-one', 'foo/baz/sibling.md', set(), files) == 404

    assert plugin.get_url_status('foo/bar/nested.html#nested-one', 'index.md', set(), files) == 0
    assert plugin.get_url_status('foo/baz/nested.html#nested-two', 'index.md', set(), files) == 0

    assert plugin.get_url_status('/index.html', 'foo/baz/sibling.md', set(), files) == 0


@patch.object(htmlproofer.plugin, "log_warning", autospec=True)
def test_get_url_status__excluded_non_existing_relative_url__no_warning(log_warning_mock, plugin):
    url_status = 404
    url = "non-existing.html"
    src_path = "index.md"
    files = {}
    plugin.config['raise_error_excludes'][url_status] = [url]

    status = plugin.get_url_status(url, src_path, set(), files)

    log_warning_mock.assert_not_called()
    assert 0 == status


@patch.object(htmlproofer.plugin, "log_warning", autospec=True)
def test_get_url_status__excluded_existing_relative_url__no_warning(log_warning_mock, plugin):
    url_status = 404
    filename = "existing"
    url = f"{filename}.html"
    src_path = f"{filename}.md"
    existing_page = Mock(spec=Page, markdown='')
    files = {
        os.path.normpath(file.url): file for file in Files([
            Mock(spec=File, src_path=src_path, dest_path=url, dest_uri=url, url=url, src_uri=src_path, page=existing_page)
        ])
    }
    plugin.config['raise_error_excludes'][url_status] = [url]

    status = plugin.get_url_status(url, src_path, set(), files)

    log_warning_mock.assert_not_called()
    assert 0 == status


@patch.object(htmlproofer.plugin, "log_warning", autospec=True)
def test_get_url_status__non_existing_relative_url__warning_and_404(log_warning_mock, plugin):
    expected_url_status = 404
    url = "non-existing.html"
    src_path = "index.md"
    files = {}

    status = plugin.get_url_status(url, src_path, set(), files)

    log_warning_mock.assert_called_once()
    assert expected_url_status == status


def test_report_invalid_url__raise_error__highest_priority(plugin):
    plugin.config['raise_error'] = True
    plugin.config['raise_error_after_finish'] = True

    with pytest.raises(PluginError):
        plugin.report_invalid_url(url='', url_status=404, src_path="")


@patch.object(htmlproofer.plugin, "log_error", autospec=True)
@patch.object(htmlproofer.plugin, "log_warning", autospec=True)
def test_report_invalid_url__raise_error__raises_and_no_log(log_warning_mock, log_error_mock, plugin):
    plugin.config['raise_error'] = True

    with pytest.raises(PluginError):
        plugin.report_invalid_url(url='', url_status=404, src_path="")
    log_warning_mock.assert_not_called()
    log_error_mock.assert_not_called()


@patch.object(htmlproofer.plugin, "log_error", autospec=True)
@patch.object(htmlproofer.plugin, "log_warning", autospec=True)
def test_report_invalid_url__raise_error_after_finish__log_error_is_called(log_warning_mock, log_error_mock, plugin):
    plugin.config['raise_error'] = False
    plugin.config['raise_error_after_finish'] = True

    plugin.report_invalid_url(url='', url_status=404, src_path="")

    log_warning_mock.assert_not_called()
    log_error_mock.assert_called_once()
    assert plugin.invalid_links


@patch.object(htmlproofer.plugin, "log_error", autospec=True)
@patch.object(htmlproofer.plugin, "log_warning", autospec=True)
def test_report_invalid_url__not_raise_error__only_log_warning_is_called(log_warning_mock, log_error_mock, plugin):
    plugin.config['raise_error'] = False
    plugin.config['raise_error_after_finish'] = False

    plugin.report_invalid_url(url='', url_status=404, src_path="")

    log_warning_mock.assert_called_once()
    log_error_mock.assert_not_called()
    assert not plugin.invalid_links


def mock_response(status_code):
    return Mock(spec=Response, status_code=status_code, iter_content=Mock(return_value=[b'content']))


@pytest.mark.parametrize(
    'exception, expected_status', [
        (requests.exceptions.InvalidURL("No host supplied"), -1),
        (requests.exceptions.ConnectionError(), -1),
        (requests.exceptions.TooManyRedirects(), -1),
        (requests.exceptions.ConnectTimeout(), 504),
        (requests.exceptions.ReadTimeout(), 504),
    ]
)
def test_resolve_web_scheme__request_exception(plugin, mock_requests, exception, expected_status):
    mock_requests.side_effect = exception

    assert plugin.resolve_web_scheme('http://') == expected_status


@pytest.mark.parametrize('skip_downloads', (False, True))
def test_resolve_web_scheme__response_is_closed(mock_requests, skip_downloads):
    plugin = HtmlProoferPlugin()
    plugin.load_config({'skip_downloads': skip_downloads})
    response = mock_response(200)
    mock_requests.side_effect = [response]

    assert plugin.resolve_web_scheme('https://example.com') == 200
    assert response.iter_content.called != skip_downloads
    response.close.assert_called_once()


def test_resolve_web_scheme__download_error(plugin, mock_requests):
    response = mock_response(200)
    response.iter_content.side_effect = requests.exceptions.ChunkedEncodingError()
    mock_requests.side_effect = [response]

    assert plugin.resolve_web_scheme('https://example.com') == -1
    response.close.assert_called_once()


@patch.object(htmlproofer.plugin.time, "sleep", autospec=True)
def test_resolve_web_scheme__retry_until_success(sleep_mock, mock_requests):
    plugin = HtmlProoferPlugin()
    plugin.load_config({'retry_max_times': 3})
    mock_requests.side_effect = [
        mock_response(503),
        requests.exceptions.ConnectionError(),
        mock_response(200),
    ]

    assert plugin.resolve_web_scheme('https://example.com') == 200
    assert mock_requests.call_count == 3
    assert [c.args for c in sleep_mock.call_args_list] == [(2,), (4,)]


@patch.object(htmlproofer.plugin.time, "sleep", autospec=True)
def test_resolve_web_scheme__retry_gives_up(sleep_mock, mock_requests):
    plugin = HtmlProoferPlugin()
    plugin.load_config({'retry_max_times': 2})
    mock_requests.side_effect = [mock_response(503), mock_response(503), mock_response(503)]

    assert plugin.resolve_web_scheme('https://example.com') == 503
    assert mock_requests.call_count == 3
    assert sleep_mock.call_count == 2


@patch.object(htmlproofer.plugin.time, "sleep", autospec=True)
def test_resolve_web_scheme__no_retry_for_excluded_status(sleep_mock, mock_requests):
    plugin = HtmlProoferPlugin()
    plugin.load_config({
        'retry_max_times': 2,
        'raise_error_excludes': {503: ['https://example.com/*']},
    })
    mock_requests.side_effect = [mock_response(503)]

    assert plugin.resolve_web_scheme('https://example.com/excluded') == 503
    sleep_mock.assert_not_called()


@patch.object(htmlproofer.plugin.time, "sleep", autospec=True)
def test_resolve_web_scheme__no_retry_for_malformed_url(sleep_mock, mock_requests):
    plugin = HtmlProoferPlugin()
    plugin.load_config({'retry_max_times': 3})
    mock_requests.side_effect = requests.exceptions.InvalidURL("No host supplied")

    assert plugin.resolve_web_scheme('http://') == -1
    assert mock_requests.call_count == 1
    sleep_mock.assert_not_called()


@patch.object(htmlproofer.plugin.time, "sleep", autospec=True)
def test_check_url__retried_url_is_not_reported(sleep_mock, mock_requests):
    plugin = HtmlProoferPlugin()
    plugin.load_config({'retry_max_times': 1, 'raise_error': True})
    mock_requests.side_effect = [mock_response(503), mock_response(200)]

    plugin.check_url('https://example.com', 'index.md', set(), {})
    # The final status is cached, so checking the URL again doesn't make any request
    plugin.check_url('https://example.com', 'other.md', set(), {})

    assert mock_requests.call_count == 2
    sleep_mock.assert_called_once_with(2)


@patch.object(htmlproofer.plugin.time, "sleep", autospec=True)
def test_check_url__local_url_is_not_retried(sleep_mock, plugin):
    plugin.config['retry_max_times'] = 3
    plugin.config['raise_error'] = True

    with pytest.raises(PluginError):
        plugin.check_url('non-existing.html', 'index.md', set(), {})
    sleep_mock.assert_not_called()
