import concurrent.futures
import fnmatch
from functools import lru_cache, partial
import os.path
import pathlib
import re
import threading
import time
from typing import Dict, List, Optional, Set
import urllib.parse
import uuid

from bs4 import BeautifulSoup, SoupStrainer
from markdown.extensions.toc import slugify
from mkdocs import utils
from mkdocs.config import Config, config_options
from mkdocs.exceptions import PluginError
from mkdocs.plugins import BasePlugin
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page
import requests
import urllib3

URL_TIMEOUT = 10.0
_URL_BOT_ID = f'Bot {uuid.uuid4()}'
URL_HEADERS = {'User-Agent': _URL_BOT_ID, 'Accept-Language': '*'}
NAME = "htmlproofer"

MARKDOWN_ANCHOR_PATTERN = re.compile(r'([^#]+)(#(.+))?')
HEADING_PATTERN = re.compile(r'\s*#+\s*(.*)')
SETEXT_UNDERLINE_PATTERN = re.compile(r' {0,3}(?:=+|-+)\s*$')
FENCE_PATTERN = re.compile(r'\s*(`{3,}|~{3,})')
HTML_LINK_PATTERN = re.compile(r'<a (?:id|name)=\"([^\"]+)\">')
# A destination, which may hold one level of balanced parentheses, e.g. (foo_(bar).png), or a
# reference, e.g. [ref]. An escaped bracket doesn't open an image or a link, but renders literally.
_DESTINATION = r'(?:\((?:[^\(\)]|\([^\(\)]*\))*\)|\[[^\]]*\])'
_IMAGE = rf'(?<!\\)\!\[[^\]]*\]{_DESTINATION}'
# An image, optionally wrapped in a link, e.g. ![alt](src), ![alt][ref], [![alt](src)](href)
# or [![alt][ref]][target]
IMAGE_PATTERN = re.compile(rf'(?<!\\)\[{_IMAGE}\]{_DESTINATION}?|{_IMAGE}')
# A link, e.g. [text](href) or [text][ref], of which only the text is rendered
LINK_PATTERN = re.compile(rf'(?<!\\)\[([^\]]*)\]{_DESTINATION}')
LOCAL_PATTERNS = [
    re.compile(rf'https?://{local}')
    for local in ('localhost', '127.0.0.1', 'app_server')
]
ATTRLIST_PATTERN = re.compile(r'\{.*?\}')
# Patterns used only without `strict_anchors`, to keep accepting what 1.5.0 accepted
ATTRLIST_ANCHOR_PATTERN = re.compile(r'\{.*?\#([^\s\}]*).*?\}')
LEGACY_IMAGE_PATTERN = re.compile(r'\[\!\[.*\]\(.*\)\].*|\!\[.*\]\[.*\].*')
# An attribute list is applied to a heading when it ends it, as in attr_list's own HEADER_RE
HEADING_ATTRLIST_PATTERN = re.compile(r'[ ]+\{\:?([^\}\n]*)\}[ ]*$')
# An id is a whole token within an attribute list, so a `#` inside e.g. {title="#tooltip"} isn't one
ATTRLIST_ID_PATTERN = re.compile(r'(?:^|[\s\{\:])\#([^\s\}]+)')
# An attribute list also applies to an inline element it directly follows, which ends with one of
# these characters, or with the `>` of an autolink. It isn't applied after raw HTML like </em>.
ELEMENT_END_CHARS = ')]*_`'
AUTOLINK_END_PATTERN = re.compile(r'<[A-Za-z][A-Za-z0-9+.\-]*\:[^<>\s]*>$|<[^<>\s@]+@[^<>\s]+>$')
# Python-Markdown strips a heading's optional closing sequence of #'s before applying attr_list
CLOSING_HASHES_PATTERN = re.compile(r'\s*\#+\s*$')

# Example emojis:
#   :banana:
#   :smiley_cat:
#   :octicons-apps-16:
#   :material-star:
EMOJI_PATTERN = re.compile(r'\:[a-z0-9_-]+\:')

# Errors for URLs that can't be requested at all, so retrying them can't succeed
MALFORMED_URL_ERRORS = (
    requests.exceptions.InvalidURL,
    requests.exceptions.InvalidSchema,
    requests.exceptions.MissingSchema,
)

urllib3.disable_warnings()


def log_info(msg, *args, **kwargs):
    utils.log.info(f"{NAME}: {msg}", *args, **kwargs)


def log_warning(msg, *args, **kwargs):
    utils.log.warning(f"{NAME}: {msg}", *args, **kwargs)


def log_error(msg, *args, **kwargs):
    utils.log.error(f"{NAME}: {msg}", *args, **kwargs)


class HtmlProoferPlugin(BasePlugin):
    files: List[File]
    invalid_links = False

    config_scheme = (
        ("enabled", config_options.Type(bool, default=True)),
        ('raise_error', config_options.Type(bool, default=False)),
        ('raise_error_after_finish', config_options.Type(bool, default=False)),
        ('raise_error_excludes', config_options.Type(dict, default={})),
        ('skip_downloads', config_options.Type(bool, default=False)),
        ('validate_external_urls', config_options.Type(bool, default=True)),
        ('validate_rendered_template', config_options.Type(bool, default=False)),
        ('strict_anchors', config_options.Type(bool, default=False)),
        ('ignore_urls', config_options.Type(list, default=[])),
        ('warn_on_ignored_urls', config_options.Type(bool, default=False)),
        ('ignore_pages', config_options.Type(list, default=[])),
        ('retry_max_times', config_options.Type(int, default=0)),
        ('max_workers', config_options.Type(int, default=None)),
    )

    def __init__(self):
        self._local = threading.local()
        self.files = []
        self.scheme_handlers = {
            "http": partial(HtmlProoferPlugin.resolve_web_scheme, self),
            "https": partial(HtmlProoferPlugin.resolve_web_scheme, self),
        }
        super().__init__()

    def _get_session(self) -> requests.Session:
        """Return a per-thread `requests.Session`, creating one lazily if needed."""
        session = getattr(self._local, 'session', None)
        if session is None:
            session = requests.Session()
            session.verify = False
            session.headers.update(URL_HEADERS)
            session.max_redirects = 5
            self._local.session = session
        return session

    def on_post_build(self, config: Config) -> None:
        if self.config['raise_error_after_finish'] and self.invalid_links:
            raise PluginError("Invalid links present.")

    def on_files(self, files: Files, config: Config) -> None:
        # Store files to allow inspecting Markdown files in later stages.
        # The values in files at this point are not guaranteed to be the same as the ones in the Page objects.
        # For example, material blog plugin may modify the files after this event.
        for f in files:
            self.files.append(f)

    def on_post_page(self, output_content: str, page: Page, config: Config) -> None:
        if not self.config['enabled']:
            return

        # Optimization: At this point, we have all the files, so we can create
        # a dictionary for faster lookups. Prior to this point, files are
        # still being updated so creating a dictionary before now would result
        # in incorrect values appearing as the key.
        opt_files = {}
        opt_files.update({os.path.normpath(file.url): file for file in self.files})
        opt_files.update({os.path.normpath(file.src_uri): file for file in self.files})

        # Optimization: only parse links and headings
        # li, sup are used for footnotes
        strainer = SoupStrainer(('a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'sup', 'img'))

        content = output_content if self.config['validate_rendered_template'] else page.content
        soup = BeautifulSoup(str(content), 'html.parser', parse_only=strainer)

        all_element_ids = set(str(tag['id']) for tag in soup.select('[id]'))
        all_element_ids.add('')  # Empty anchor is commonly used, but not real

        urls = (set(str(a['href']) for a in soup.find_all('a', href=True)) |
                set(str(img['src']) for img in soup.find_all('img', src=True)))

        urls_to_check: List[str] = []
        for url in urls:
            if any(fnmatch.fnmatch(url, ignore_url) for ignore_url in self.config['ignore_urls']):
                if self.config['warn_on_ignored_urls']:
                    log_warning(f"ignoring URL {url} from {page.file.src_path}")
            elif any(
                fnmatch.fnmatch(page.file.src_path, ignore_page)
                for ignore_page in self.config['ignore_pages']
            ):
                if self.config['warn_on_ignored_urls']:
                    log_warning(f"ignoring URL {url} from {page.file.src_path}")
            else:
                urls_to_check.append(url)

        # Note on exception propagation: `future.result()` re-raises any exception
        # from a worker thread. If `raise_error` is `True` and multiple URLs fail
        # concurrently, only the first exception to be observed here will propagate;
        # remaining futures continue to execute but their exceptions are not raised.
        # This is acceptable because each thread independently logs/reports its
        # failure via `report_invalid_url` before raising, so no errors are silently
        # lost. When `raise_error_after_finish` is used instead, all failures are
        # recorded via the `invalid_links` flag and surfaced in `on_post_build`.
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config['max_workers']) as executor:
            for future in concurrent.futures.as_completed(
                executor.submit(self.check_url, url, page.file.src_path, all_element_ids, opt_files) for url in urls_to_check
            ):
                future.result()

    def report_invalid_url(self, url, url_status, src_path):
        error = f'invalid url - {url} [{url_status}] [{src_path}]'
        if self.config['raise_error']:
            raise PluginError(error)
        elif self.config['raise_error_after_finish']:
            log_error(error)
            self.invalid_links = True
        else:
            log_warning(error)

    def get_external_url(self, url, scheme, src_path):
        try:
            return self.scheme_handlers[scheme](url)
        except KeyError:
            log_info(f'Unknown url-scheme "{scheme}:" detected. "{url}" from "{src_path}" will not be checked.')
        return 0

    @lru_cache(maxsize=1000)
    def resolve_web_scheme(self, url: str) -> int:
        # Retry here rather than in `check_url`, so that only web URLs are retried
        # and a transient failure is not cached (and replayed) as the final status.
        try:
            url_status = self.fetch_web_url_status(url)
            retry_duration = 2
            for _ in range(self.config['retry_max_times']):
                if not (self.bad_url(url_status) and self.is_error(self.config, url, url_status)):
                    break
                log_info(f"Retrying URL {url} after {retry_duration} seconds...")
                time.sleep(retry_duration)
                retry_duration *= 2
                url_status = self.fetch_web_url_status(url)
            return url_status
        except MALFORMED_URL_ERRORS:
            return -1

    def fetch_web_url_status(self, url: str) -> int:
        try:
            response = self._get_session().get(url, timeout=URL_TIMEOUT, stream=True)
            try:
                if self.config['skip_downloads'] is False:
                    # Download the entire contents as to not break previous behaviour.
                    for _ in response.iter_content(chunk_size=1024 * 1024):
                        pass

                return response.status_code
            finally:
                # Release the connection, which is kept open by `stream=True` otherwise.
                response.close()
        except requests.exceptions.Timeout:
            return 504
        except MALFORMED_URL_ERRORS:
            raise
        except requests.exceptions.RequestException:
            # e.g. ConnectionError, TooManyRedirects, InvalidURL, ChunkedEncodingError
            return -1

    def check_url(
            self,
            url: str,
            src_path: str,
            all_element_ids: Set[str],
            files: Dict[str, File],
            ) -> None:
        url_status = self.get_url_status(url, src_path, all_element_ids, files)
        if self.bad_url(url_status) and self.is_error(self.config, url, url_status):
            self.report_invalid_url(url, url_status, src_path)

    def get_url_status(
            self,
            url: str,
            src_path: str,
            all_element_ids: Set[str],
            files: Dict[str, File]
    ) -> int:
        if any(pat.match(url) for pat in LOCAL_PATTERNS):
            return 0

        scheme, _, path, _, fragment = urllib.parse.urlsplit(url)
        if scheme:
            if self.config['validate_external_urls']:
                return self.get_external_url(url, scheme, src_path)
            return 0
        if fragment and not path:
            return 0 if url[1:] in all_element_ids else 404
        else:
            is_valid = self.is_url_target_valid(url, src_path, files, self.config['strict_anchors'])
            url_status = 404
            if not is_valid and self.is_error(self.config, url, url_status):
                log_warning(f"Unable to locate source file for: {url}")
                return url_status
            return 0

    @staticmethod
    def is_url_target_valid(url: str, src_path: str, files: Dict[str, File],
                            strict_anchors: bool = False) -> bool:
        match = MARKDOWN_ANCHOR_PATTERN.match(url)
        if match is None:
            return True

        url_target, _, optional_anchor = match.groups()
        source_file = HtmlProoferPlugin.find_source_file(url_target, src_path, files)
        if source_file is None:
            return False

        # If there's an anchor (fragment) on the link, we try to find it in the source_file
        if optional_anchor:
            _, extension = os.path.splitext(source_file.src_uri)
            # Currently only Markdown-based pages are supported, but conceptually others could be added below
            if extension == ".md":
                if source_file.page is None or source_file.page.markdown is None:
                    return False
                if not HtmlProoferPlugin.contains_anchor(source_file.page.markdown, optional_anchor,
                                                         strict_anchors):
                    return False

        return True

    @staticmethod
    def find_target_markdown(url: str, src_path: str, files: Dict[str, File]) -> Optional[str]:
        """From a built URL, find the original Markdown source from the project that built it."""

        file = HtmlProoferPlugin.find_source_file(url, src_path, files)
        if file and file.page:
            return file.page.markdown
        return None

    @staticmethod
    def find_source_file(url: str, src_path: str, files: Dict[str, File]) -> Optional[File]:
        """From a built URL, find the original file from the project that built it."""

        if len(url) > 1 and url[0] == '/':
            # Convert root/site paths
            search_path = os.path.normpath(url[1:])
        else:
            # Handle relative links by looking up the destination url for the
            # src_path and getting the parent directory.
            try:
                dest_uri = files[src_path].dest_uri
                src_dir = urllib.parse.quote(str(pathlib.Path(dest_uri).parent), safe='/\\')
                search_path = os.path.normpath(str(pathlib.Path(src_dir) / pathlib.Path(url)))
            except KeyError:
                return None

        try:
            return files[search_path]
        except KeyError:
            return None

    @staticmethod
    def contains_anchor(markdown: str, anchor: str, strict_anchors: bool = False) -> bool:
        """Check if a set of Markdown source text contains a heading that corresponds to a
        given anchor."""
        lines = markdown.splitlines()
        if strict_anchors:
            # Headings and anchors within code blocks aren't rendered, so they provide no anchor
            lines = HtmlProoferPlugin.blank_fenced_code(lines)
        previous_line = ''
        for line in lines:
            # Markdown allows whitespace before headers and an arbitrary number of #'s.
            heading_match = HEADING_PATTERN.match(line)
            if heading_match is not None:
                # Only an ATX heading has an optional closing sequence of #'s, which isn't rendered
                heading = CLOSING_HASHES_PATTERN.sub('', heading_match.group(1))
                if HtmlProoferPlugin.heading_matches_anchor(heading, anchor, strict_anchors):
                    return True
            elif HtmlProoferPlugin.underlines_setext_heading(line, previous_line, strict_anchors):
                if HtmlProoferPlugin.heading_matches_anchor(previous_line.strip(), anchor, strict_anchors):
                    return True
            previous_line = line

            # Check for HTML anchors using id or name attributes
            # Multiple anchors can exist on a single line, so find all of them
            for html_anchor in re.findall(HTML_LINK_PATTERN, line):
                if anchor == html_anchor:
                    return True

            # Any attribute list at end of paragraphs or after images can also generate an anchor (in addition to
            # the heading ones) so gather those and check as well (multiple could be a line so gather all)
            if anchor in HtmlProoferPlugin.attr_list_anchors(line, strict_anchors):
                return True

        return False

    @staticmethod
    def underlines_setext_heading(line: str, previous_line: str, strict_anchors: bool) -> bool:
        """Check if a line of ='s or -'s underlines the previous one, making it a Setext heading."""
        if not previous_line.strip() or SETEXT_UNDERLINE_PATTERN.match(line) is None:
            return False
        # An indented line is code rather than heading text
        return not (strict_anchors and previous_line.startswith(('    ', '\t')))

    @staticmethod
    def blank_fenced_code(lines: List[str]) -> List[str]:
        """Blank out the lines of fenced code blocks, which don't generate any headings or anchors."""
        lines = list(lines)
        start: Optional[int] = None
        fence = ''
        for i, line in enumerate(lines):
            match = FENCE_PATTERN.match(line)
            if match is None:
                continue
            if start is None:
                start, fence = i, match.group(1)
            elif match.group(1).startswith(fence) and not line[match.end():].strip():
                # A fence is closed by at least as many of the same characters. Unclosed fences are
                # left alone, as they aren't rendered as code blocks.
                lines[start:i + 1] = [''] * (i + 1 - start)
                start = None
        return lines

    @staticmethod
    def legacy_heading_anchor(heading: str) -> str:
        """The anchor 1.5.0 generated for a heading, which is accepted without `strict_anchors`."""
        heading = re.sub(ATTRLIST_PATTERN, '', heading)
        heading = re.sub(LEGACY_IMAGE_PATTERN, '', heading)
        heading = re.sub(EMOJI_PATTERN, '', heading)
        return slugify(heading, '-')

    @staticmethod
    def follows_inline_element(before: str) -> bool:
        """Check if an attribute list directly following this text is applied to an inline element."""
        return bool(before) and (before[-1] in ELEMENT_END_CHARS
                                 or AUTOLINK_END_PATTERN.search(before) is not None)

    @staticmethod
    def remove_inline_attr_lists(text: str) -> str:
        """Remove the attribute lists applied to inline elements, which aren't rendered as text."""
        parts = []
        end = 0
        for match in ATTRLIST_PATTERN.finditer(text):
            if HtmlProoferPlugin.follows_inline_element(text[:match.start()]):
                parts.append(text[end:match.start()])
                end = match.end()
        parts.append(text[end:])
        return ''.join(parts)

    @staticmethod
    def attr_list_anchors(line: str, strict_anchors: bool = False) -> List[str]:
        """Find the anchors set by attribute lists in a line of Markdown."""
        # Without `strict_anchors`, an id anywhere in an attribute list counts, as it did in 1.5.0
        anchors = [] if strict_anchors else re.findall(ATTRLIST_ANCHOR_PATTERN, line)
        matches = list(ATTRLIST_PATTERN.finditer(line))
        follows_element = [HtmlProoferPlugin.follows_inline_element(line[:m.start()]) for m in matches]
        # Each cell of a table row is its own element, so count the lists ending one per cell
        cells = [line.count('|', 0, m.start()) for m in matches]
        ending_cells = [cell for cell, follows in zip(cells, follows_element) if not follows]
        for match, follows, cell in zip(matches, follows_element, cells):
            anchor_match = ATTRLIST_ID_PATTERN.search(match.group())
            if anchor_match is None:
                continue
            before, after = line[:match.start()], line[match.end():]
            # An attribute list only applies when it directly follows an inline element, stands on its
            # own line, or ends a heading or table cell. Otherwise, it's rendered as literal text.
            own_line = not before.strip()
            # attr_list needs a space before a list which ends an element, and applies none of several
            ends_element = before[-1:].isspace() and (not after.strip() or after.lstrip().startswith('|'))
            only_one = ending_cells.count(cell) == 1
            if follows or ((own_line or ends_element) and only_one):
                anchors.append(anchor_match.group(1))
        return anchors

    @staticmethod
    def heading_matches_anchor(heading: str, anchor: str, strict_anchors: bool = False) -> bool:
        """Check if a Markdown heading text corresponds to a given anchor."""
        if not strict_anchors and anchor == HtmlProoferPlugin.legacy_heading_anchor(heading):
            return True

        # Headings are allowed to have attr_list after them, of the form:
        # # Heading { #testanchor .testclass }
        # # Heading {: #testanchor .testclass }
        # # Heading {.testclass #testanchor}
        # # Heading {.testclass}
        # these can override the headings anchor id, or alternatively just provide additional class etc.
        # attr_list applies every list directly following an inline element, and a single one at the end
        # of the heading. Any other one, e.g. a leading list, is rendered as literal text and slugified.
        heading = HtmlProoferPlugin.remove_inline_attr_lists(heading)
        if len(ATTRLIST_PATTERN.findall(heading)) == 1:
            heading_attr_list = HEADING_ATTRLIST_PATTERN.search(heading)
            if heading_attr_list is not None:
                heading_id = ATTRLIST_ID_PATTERN.search(heading_attr_list.group(1))
                if heading_id is not None:
                    # The id replaces the slug which would otherwise be generated
                    return anchor == heading_id.group(1)
                heading = heading[:heading_attr_list.start()]

        # Headings are allowed to have images after them, of the form:
        # # Heading [![Image](image-link)] or ![Image][image-reference]
        # But these images are not included in the generated anchor, so remove them.
        heading = re.sub(IMAGE_PATTERN, '', heading)

        # Of a link, only its text is rendered into the heading, not its target.
        heading = LINK_PATTERN.sub(r'\1', heading)

        # Headings are allowed to have emojis in them under certain Mkdocs themes.
        # https://squidfunk.github.io/mkdocs-material/setup/extensions/python-markdown-extensions/#emoji
        heading = re.sub(EMOJI_PATTERN, '', heading)

        return anchor == slugify(heading, '-')

    @staticmethod
    def bad_url(url_status: int) -> bool:
        if url_status == -1:
            return True
        elif url_status >= 400:
            return True
        else:
            return False

    @staticmethod
    def is_error(config: Config, url: str, url_status: int) -> bool:
        excludes = config['raise_error_excludes'].get(url_status, [])

        if any(fnmatch.fnmatch(url, exclude_url) for exclude_url in excludes):
            return False
        else:
            return True
