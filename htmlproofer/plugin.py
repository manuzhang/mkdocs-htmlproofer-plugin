import concurrent.futures
import fnmatch
from functools import lru_cache, partial
import os.path
import pathlib
import re
import threading
import time
from typing import Dict, FrozenSet, List, Optional, Set, Tuple, Union
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
LOCAL_PATTERNS = [
    re.compile(rf'https?://{local}')
    for local in ('localhost', '127.0.0.1', 'app_server')
]

# Patterns for the anchors a Markdown source provides, which are accepted without `strict_anchors`
HEADING_PATTERN = re.compile(r'\s*#+\s*(.*)')
SETEXT_UNDERLINE_PATTERN = re.compile(r' {0,3}(?:=+|-+)\s*$')
HTML_LINK_PATTERN = re.compile(r'<a (?:id|name)=\"([^\"]+)\">')
ATTRLIST_PATTERN = re.compile(r'\{.*?\}')
ATTRLIST_ANCHOR_PATTERN = re.compile(r'\{.*?\#([^\s\}]*).*?\}')
IMAGE_PATTERN = re.compile(r'\[\!\[.*\]\(.*\)\].*|\!\[.*\]\[.*\].*')

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


@lru_cache(maxsize=1024)
def parse_anchors(rendered_content: str) -> FrozenSet[str]:
    """The anchors of a page kept elsewhere, such as a page's body, parsed once per page.

    The cache holds what it is given, so only pass content something else already retains."""
    return read_anchors(rendered_content)


def read_anchors(rendered_content: str) -> FrozenSet[str]:
    """The anchors a rendered page provides, which are the ids and anchor names it contains."""
    soup = BeautifulSoup(rendered_content, 'html.parser')
    # A template's contents are inert, so a fragment can't navigate to the ids within it, though
    # the template element itself stays in the document and keeps its own id

    def navigable(tag) -> bool:
        return tag.find_parent('template') is None

    # `name` makes an anchor navigable, as an older form of `id`
    return frozenset({str(tag['id']) for tag in soup.select('[id]') if navigable(tag)}
                     | {str(tag['name']) for tag in soup.select('a[name]') if navigable(tag)})


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
        # The anchors each rendered page provides, by source path
        self.rendered_pages: Dict[str, FrozenSet[str]] = {}
        # Links whose target hadn't been rendered when they were checked, by source path
        self.deferred_urls: List[Tuple[str, str]] = []
        self.scheme_handlers = {
            "http": partial(HtmlProoferPlugin.resolve_web_scheme, self),
            "https": partial(HtmlProoferPlugin.resolve_web_scheme, self),
        }
        super().__init__()

    def file_index(self) -> Dict[str, File]:
        """The files of the site, looked up by the URL they are built to and by their source."""
        index: Dict[str, File] = {}
        index.update({os.path.normpath(file.url): file for file in self.files})
        index.update({os.path.normpath(file.src_uri): file for file in self.files})
        return index

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
        # Every page has been rendered by now, so the links whose target was still being built
        # can be settled against the anchors it turned out to provide. One index serves them all.
        deferred, self.deferred_urls = self.deferred_urls, []
        if deferred:
            files = self.file_index()
            for url, src_path in deferred:
                self.check_url(url, src_path, set(), files, defer=False)

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
        opt_files = self.file_index()

        # Optimization: only parse links and headings
        # li, sup are used for footnotes
        strainer = SoupStrainer(('a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'sup', 'img'))

        # Keep the anchors of this page's full output, so a link into it is checked against
        # everything it renders, the theme's included, rather than its Markdown body alone. Read
        # them without the cache, which would hold the output this is replacing.
        self.rendered_pages[page.file.src_uri] = read_anchors(output_content)

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
            defer: bool = True,
            ) -> None:
        if defer and self.target_is_unrendered(url, src_path, files):
            # Only part of the target's anchors are known, so a verdict now would depend on the
            # order pages are built in, and reporting one would warn about a link which may hold.
            # Settle it once the whole page has been rendered.
            self.deferred_urls.append((url, src_path))
            return

        url_status = self.get_url_status(url, src_path, all_element_ids, files)
        if self.bad_url(url_status) and self.is_error(self.config, url, url_status):
            self.report_invalid_url(url, url_status, src_path)

    def target_is_unrendered(self, url: str, src_path: str, files: Dict[str, File]) -> bool:
        """Check if a link points at an anchor of a page which hasn't been rendered yet."""
        match = MARKDOWN_ANCHOR_PATTERN.match(url)
        if match is None or not match.groups()[2]:
            return False
        source_file = HtmlProoferPlugin.find_source_file(match.groups()[0], src_path, files)
        return source_file is not None and source_file.src_uri not in self.rendered_pages

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
            # A fragment is percent-encoded in the URL, while an id is written as it renders
            return 0 if urllib.parse.unquote(url[1:]) in all_element_ids else 404
        else:
            is_valid = self.is_url_target_valid(url, src_path, files, self.config['strict_anchors'],
                                                self.rendered_pages)
            url_status = 404
            if not is_valid and self.is_error(self.config, url, url_status):
                log_warning(f"Unable to locate source file for: {url}")
                return url_status
            return 0

    @staticmethod
    def is_url_target_valid(url: str, src_path: str, files: Dict[str, File],
                            strict_anchors: bool = False,
                            rendered_pages: Optional[Dict[str, FrozenSet[str]]] = None) -> bool:
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
                # A page's full output covers the anchors its theme renders as well, and is there
                # once it has been built; until then its Markdown body is what's available
                anchors = (rendered_pages or {}).get(source_file.src_uri)
                if anchors is None:
                    anchors = HtmlProoferPlugin.rendered_anchors(
                        getattr(source_file.page, 'content', None))
                # A fragment is percent-encoded in the URL, while an id is written as it renders
                anchor = urllib.parse.unquote(optional_anchor)
                if not HtmlProoferPlugin.contains_anchor(source_file.page.markdown, anchor,
                                                         anchors, strict_anchors):
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
    def contains_anchor(markdown: str, anchor: str,
                        anchors: Union[str, FrozenSet[str], None] = None,
                        strict_anchors: bool = False) -> bool:
        """Check if a page provides an anchor, from those of its rendered HTML.

        The rendered page is given either as its anchors or as its HTML. With `strict_anchors`,
        only those anchors count. Without it, the anchors its Markdown source provides are
        accepted as well."""
        if isinstance(anchors, str):
            anchors = HtmlProoferPlugin.rendered_anchors(anchors)
        if anchors and anchor in anchors:
            return True
        if strict_anchors and anchors is not None:
            return False
        # The page hasn't been rendered yet, or its source anchors are accepted too
        return HtmlProoferPlugin.source_contains_anchor(markdown, anchor)

    @staticmethod
    def rendered_anchors(rendered_content: Optional[str]) -> FrozenSet[str]:
        """The anchors a rendered page provides, which are the ids and anchor names it contains."""
        if not rendered_content:
            return frozenset()
        return parse_anchors(rendered_content)

    @staticmethod
    def source_contains_anchor(markdown: str, anchor: str) -> bool:
        """Check the Markdown source of a page for an anchor."""
        previous_line = ''
        for line in markdown.splitlines():
            # Markdown allows whitespace before headers and an arbitrary number of #'s.
            heading_match = HEADING_PATTERN.match(line)
            if heading_match is not None and anchor == HtmlProoferPlugin.heading_anchor(
                    heading_match.group(1)):
                return True

            # A heading may instead be underlined with ='s or -'s on the line below it
            if (previous_line.strip() and SETEXT_UNDERLINE_PATTERN.match(line)
                    and anchor == HtmlProoferPlugin.heading_anchor(previous_line.strip())):
                return True
            previous_line = line

            # Check for HTML anchors using id or name attributes
            # Multiple anchors can exist on a single line, so find all of them
            if anchor in re.findall(HTML_LINK_PATTERN, line):
                return True

            # Any attribute list at end of paragraphs or after images can also generate an anchor (in
            # addition to the heading ones) so gather those and check as well
            if anchor in re.findall(ATTRLIST_ANCHOR_PATTERN, line):
                return True

        return False

    @staticmethod
    def heading_anchor(heading: str) -> str:
        """The anchor a heading provides, slugified from its Markdown source."""
        heading = re.sub(ATTRLIST_PATTERN, '', heading)
        heading = re.sub(IMAGE_PATTERN, '', heading)
        heading = re.sub(EMOJI_PATTERN, '', heading)
        return slugify(heading, '-')

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
