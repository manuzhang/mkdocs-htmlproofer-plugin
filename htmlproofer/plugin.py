from functools import lru_cache
import re
import sys
from typing import Optional, Set, Tuple
import uuid

from bs4 import BeautifulSoup, SoupStrainer
from markdown.extensions.toc import slugify
from mkdocs.config import Config, config_options
from mkdocs.exceptions import PluginError
from mkdocs.plugins import BasePlugin
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page
import requests
import urllib3

URL_TIMEOUT = 10.0
_URL_BOT_ID = f'Bot {uuid.uuid4()}'
URL_HEADERS = {'User-Agent': _URL_BOT_ID}

EXTERNAL_URL_PATTERN = re.compile(r'https?://')
MARKDOWN_ANCHOR_PATTERN = re.compile(r'(.+)#(.+)')
HEADING_PATTERN = re.compile(r'\s*#+\s*(.*)')
HTML_LINK_PATTERN = re.compile(r'.*<a id=\"(.*)\">.*')
LOCAL_PATTERNS = (
    re.compile(rf'https?://{local}')
    for local in ('localhost', '127.0.0.1', 'app_server')
)

urllib3.disable_warnings()


class HtmlProoferPlugin(BasePlugin):
    files: Files = None

    config_scheme = (
        ('raise_error', config_options.Type(bool, default=False)),
        ('raise_error_excludes', config_options.Type(dict, default={})),
    )

    def __init__(self):
        self._session = requests.Session()
        self._session.verify = False
        self._session.headers.update(URL_HEADERS)
        self._session.max_redirects = 5
        super().__init__()

    def on_page_markdown(self, markdown: str, page: Page, config: Config, files: Files) -> None:
        # Store files to allow inspecting Markdown files in later stages.
        self.files = files

    def on_post_page(self, output_content: str, page: Page, config: Config) -> None:
        use_directory_urls = config.data["use_directory_urls"]

        # Optimization: only parse links and headings
        # li, sup are used for footnotes
        strainer = SoupStrainer(('a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'sup'))

        soup = BeautifulSoup(output_content, 'lxml', parse_only=strainer)

        all_element_ids = set(tag['id'] for tag in soup.select('[id]'))
        all_element_ids.add('#')  # Empty anchor is commonly used, but not real
        for a in soup.find_all('a', href=True):
            url = a['href']

            clean_url, url_status = self.get_url_status(url, all_element_ids, self.files, use_directory_urls)

            if self.bad_url(url_status) is True:
                error = f'invalid url - {clean_url} [{url_status}] [{page.file.src_path}]'

                is_error = self.is_error(self.config, url, url_status)
                if self.config['raise_error'] and is_error:
                    raise PluginError(error)
                elif is_error:
                    print(error)

    @lru_cache(maxsize=1000)
    def get_external_url(self, url: str) -> int:
        try:
            response = self._session.head(url, timeout=URL_TIMEOUT)
            return response.status_code
        except requests.exceptions.Timeout:
            return 504
        except requests.exceptions.TooManyRedirects:
            return -1
        except requests.exceptions.ConnectionError:
            return -1

    def get_url_status(self, url: str, all_element_ids: Set[str], files: Files,
                       use_directory_urls: bool) -> Tuple[str, int]:
        if any(pat.match(url) for pat in LOCAL_PATTERNS):
            return url, 0
        clean_url = url.strip('?.')

        if url.startswith('#') and not url.lstrip('#') in all_element_ids:
            return url, 404
        elif EXTERNAL_URL_PATTERN.match(clean_url):
            return url, self.get_external_url(clean_url)
        elif not use_directory_urls:
            # use_directory_urls = True injects too many challenges for locating the correct target
            # Markdown file, so disable target anchor validation in this case. Examples include:
            # ../..#BAD_ANCHOR style links to index.html and extra ../ inserted into relative
            # links.

            match = MARKDOWN_ANCHOR_PATTERN.match(clean_url)
            if match is not None:
                # URL is a link to another local Markdown file that includes an anchor.
                url_target, anchor = match.groups()
                target_markdown = self.find_target_markdown(url_target, files)
                if (target_markdown is None
                        or not self.contains_anchor(target_markdown, anchor)):
                    # The corresponding Markdown header for this anchor was not found.
                    return url, 404
        return url, 0

    @staticmethod
    def find_target_markdown(url: str, files: Files) -> Optional[str]:
        """From a built URL, find the original Markdown source from the project that built it."""
        # Remove /../ relative pathing from absolute URLs to match how MkDocs stores URLs in Files.
        url = url.lstrip("/").lstrip("../")

        for file in files.src_paths.values():  # type: File
            # Using endswith() is to deal with relative URLs that do not contain the full path
            # to the .html file. This approximation will allow a small number of anchors to be
            # validated even if they don't exist (if the same Markdown filename is used in
            # multiple folders), but the alternative is to try to reimplement MkDocs file/URL
            # generation.
            if file.url.endswith(url):
                return file.page.markdown
        print(f"Warning: Unable to locate Markdown source file for: {url}", file=sys.stderr)
        return None

    @staticmethod
    def contains_anchor(markdown: str, anchor: str) -> bool:
        """Check if a set of Markdown source text contains a heading that corresponds to a
        given anchor."""
        for line in markdown.splitlines():
            # Markdown allows whitespace before headers and an arbitrary number of #'s.
            heading_match = HEADING_PATTERN.match(line)
            if heading_match is not None:
                heading = heading_match.groups()[0]

                # Headings are allowed to have images after them, of the form:
                # # Heading [![Image][image-link]]
                # But these images are not included in the generated anchor, so remove them.
                heading = heading.split("[")[0].strip()
                anchor_slug = slugify(heading, '-')
                if anchor == anchor_slug:
                    return True

            link_match = HTML_LINK_PATTERN.match(line)
            if link_match is not None and link_match.group(1) == anchor:
                return True

        return False

    @staticmethod
    def bad_url(url_status: int) -> bool:
        if url_status == -1:
            return True
        elif url_status == 401 or url_status == 403:
            return False
        elif url_status in (503, 504):
            # Usually transient
            return False
        elif url_status == 999:
            # Returned by some websites (e.g. LinkedIn) that think you're crawling them.
            return False
        elif url_status >= 400:
            return True
        return False

    @staticmethod
    def is_error(config: Config, url: str, url_status: int) -> bool:
        excludes = config['raise_error_excludes'].get(url_status, [])

        if '*' in excludes or url in excludes:
            return False

        return True
