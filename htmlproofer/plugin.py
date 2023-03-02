from functools import lru_cache
import os.path
import pathlib
import re
import sys
from typing import Dict, Optional, Set
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
URL_HEADERS = {'User-Agent': _URL_BOT_ID, 'Accept-Language': '*'}

EXTERNAL_URL_PATTERN = re.compile(r'https?://')
MARKDOWN_ANCHOR_PATTERN = re.compile(r'([^#]+)(#(.+))?')
HEADING_PATTERN = re.compile(r'\s*#+\s*(.*)')
HTML_LINK_PATTERN = re.compile(r'.*<a id=\"(.*)\">.*')
IMAGE_PATTERN = re.compile(r'\[\!\[.*\]\(.*\)\].*|\!\[.*\]\[.*\].*')
LOCAL_PATTERNS = [
    re.compile(rf'https?://{local}')
    for local in ('localhost', '127.0.0.1', 'app_server')
]

urllib3.disable_warnings()


class HtmlProoferPlugin(BasePlugin):
    files: Dict[str, File]
    invalid_links = False

    config_scheme = (
        ("enabled", config_options.Type(bool, default=True)),
        ('raise_error', config_options.Type(bool, default=False)),
        ('raise_error_after_finish', config_options.Type(bool, default=False)),
        ('raise_error_excludes', config_options.Type(dict, default={})),
        ('validate_external_urls', config_options.Type(bool, default=True)),
        ('validate_rendered_template', config_options.Type(bool, default=False)),
    )

    def __init__(self):
        self._session = requests.Session()
        self._session.verify = False
        self._session.headers.update(URL_HEADERS)
        self._session.max_redirects = 5
        self.files = {}
        super().__init__()

    def on_post_build(self, config: Config)-> None:
        if self.config['raise_error_after_finish'] and self.invalid_links:
            raise PluginError("Invalid links present.")

    def on_page_markdown(self, markdown: str, page: Page, config: Config, files: Files) -> None:
        # Store files to allow inspecting Markdown files in later stages.
        self.files.update({os.path.normpath(file.url): file for file in files})

    def on_post_page(self, output_content: str, page: Page, config: Config) -> None:
        if not self.config['enabled']:
            return

        use_directory_urls = config.data["use_directory_urls"]

        # Optimization: only parse links and headings
        # li, sup are used for footnotes
        strainer = SoupStrainer(('a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'sup', 'img'))

        content = output_content if self.config['validate_rendered_template'] else page.content
        soup = BeautifulSoup(content, 'html.parser', parse_only=strainer)

        all_element_ids = set(tag['id'] for tag in soup.select('[id]'))
        all_element_ids.add('')  # Empty anchor is commonly used, but not real
        for a in soup.find_all('a', href=True):
            url = a['href']

            url_status = self.get_url_status(url, page.file.src_path, all_element_ids, self.files, use_directory_urls)

            if self.bad_url(url_status) is True:
                error = f'invalid url - {url} [{url_status}] [{page.file.src_path}]'

                is_error = self.is_error(self.config, url, url_status)
                if self.config['raise_error'] and is_error:
                    raise PluginError(error)
                elif self.config['raise_error_after_finish'] and is_error and not self.invalid_links:
                    self.invalid_links = True
                if is_error:
                    print(error)

    @lru_cache(maxsize=1000)
    def get_external_url(self, url: str) -> int:
        try:
            response = self._session.get(url, timeout=URL_TIMEOUT)
            return response.status_code
        except requests.exceptions.Timeout:
            return 504
        except requests.exceptions.TooManyRedirects:
            return -1
        except requests.exceptions.ConnectionError:
            return -1

    def get_url_status(self, url: str, src_path: str, all_element_ids: Set[str], files: Dict[str, File],
        use_directory_urls: bool) -> int:
        if any(pat.match(url) for pat in LOCAL_PATTERNS):
            return 0

        if url.startswith('#'):
            return 0 if url[1:] in all_element_ids else 404
        elif EXTERNAL_URL_PATTERN.match(url):
            if not self.config['validate_external_urls']:
                return 0
            return self.get_external_url(url)
        elif not use_directory_urls:
            # use_directory_urls = True injects too many challenges for locating the correct target
            # Markdown file, so disable target anchor validation in this case. Examples include:
            # ../..#BAD_ANCHOR style links to index.html and extra ../ inserted into relative
            # links.
            if not self.is_url_target_valid(url, src_path, files):
                return 404
        return 0

    @staticmethod
    def is_url_target_valid(url: str, src_path: str, files: Dict[str, File]) -> bool:
        match = MARKDOWN_ANCHOR_PATTERN.match(url)
        if match is None:
            return True

        url_target, _, optional_anchor = match.groups()
        _, extension = os.path.splitext(url_target)
        if extension == ".html":
            # URL is a link to another local Markdown file that may includes an anchor.
            target_markdown = HtmlProoferPlugin.find_target_markdown(url_target, src_path, files)
            if target_markdown is None:
                # The corresponding Markdown page was not found.
                return False
            if optional_anchor and not HtmlProoferPlugin.contains_anchor(target_markdown, optional_anchor):
                # The corresponding Markdown header for this anchor was not found.
                return False
        elif HtmlProoferPlugin.find_source_file(url_target, src_path, files) is None:
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

        # Handle relative links by concatenating the source dir with the destination path
        search_path = os.path.normpath(str(pathlib.Path(src_path).parent / pathlib.Path(url)))

        try:
            return files[search_path]
        except KeyError:
            print(f"Warning: Unable to locate source file for: {url}", file=sys.stderr)
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
                # # Heading [![Image](image-link)] or ![Image][image-reference]
                # But these images are not included in the generated anchor, so remove them.
                heading = re.sub(IMAGE_PATTERN, '', heading)
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
