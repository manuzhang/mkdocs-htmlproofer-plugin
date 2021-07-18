from functools import lru_cache
import re
from typing import Optional, Tuple
import uuid

from bs4 import BeautifulSoup
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

urllib3.disable_warnings()


class HtmlProoferPlugin(BasePlugin):
    files: Files = None

    config_scheme = (
        ('raise_error', config_options.Type(bool, default=False)),
        ('raise_error_excludes', config_options.Type(dict, default={}))
    )

    def on_page_markdown(self, markdown: str, page: Page, config: Config, files: Files) -> None:
        # Store files to allow inspecting Markdown files in later stages.
        self.files = files

    def on_post_page(self, output_content: str, page: Page, config: Config) -> None:
        soup = BeautifulSoup(output_content, 'html.parser')
        for a in soup.find_all('a', href=True):
            url = a['href']
            clean_url, url_status = self.get_url_status(url, soup, self.files)
            if self.bad_url(url_status) is True:
                error = f'{clean_url}: {url_status}'
                excludes = self.config['raise_error_excludes']
                if (self.config['raise_error'] and
                        (url_status not in excludes or
                         ('*' not in excludes[url_status] and
                          url not in excludes[url_status]))):
                    raise PluginError(error)
                else:
                    print(error)

    @lru_cache(maxsize=500)
    def get_url_status(self, url: str, soup: BeautifulSoup, files: Files) -> Tuple[str, int]:
        for local in ('localhost', '127.0.0.1', 'app_server'):
            if re.match(rf'https?://{local}', url):
                return url, 0
        clean_url = url.strip('?.')
        if url.startswith('#') and not soup.find(id=url.strip('#')):
            return url, 404
        elif re.match(r'https?://', clean_url):
            try:
                response = requests.get(
                    clean_url, verify=False, timeout=URL_TIMEOUT,
                    headers=URL_HEADERS)
                return clean_url, response.status_code
            except requests.exceptions.Timeout:
                return clean_url, 504
            except requests.exceptions.ConnectionError:
                return clean_url, -1
        else:
            match = re.match(r'(.+)#(.+)', clean_url)
            if match is not None:
                # URL is a link to another local Markdown file that includes an anchor.
                url_target, anchor = match.groups()
                target_markdown = self.find_source_markdown(url_target, files)
                if (target_markdown is not None
                        and not self.contains_heading(target_markdown, anchor)):
                    # The corresponding Markdown header for this anchor was not found.
                    return url, 404

            return url, 0

    @staticmethod
    def find_source_markdown(url: str, files: Files) -> Optional[str]:
        """From a built URL, find the original Markdown source from the project that built it."""
        for file in files.src_paths.values():  # type: File
            if file.url == url:
                return file.page.markdown
        return None

    @staticmethod
    def contains_heading(markdown: str, heading: str) -> bool:
        """Check if a set of Markdown source text contains a heading."""
        for line in markdown.splitlines():
            # Markdown allows whitespace before headers and MkDocs allows extra things
            # like image links afterwards. So essentially, we are searching for "# ANCHOR*" here.
            match = re.match(rf'\s*#+\s*{heading}.*', line, re.IGNORECASE)
            if match is not None:
                return True
        return False

    @staticmethod
    def bad_url(url_status: int) -> bool:
        if url_status == -1:
            return True
        elif url_status == 401 or url_status == 403:
            return False
        elif url_status == 503:
            return False
        elif url_status == 999:
            # Returned by some websites (e.g. LinkedIn) that think you're crawling them.
            return False
        elif url_status >= 400:
            return True
        return False
