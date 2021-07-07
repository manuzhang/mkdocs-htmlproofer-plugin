from functools import lru_cache
import re
from typing import Tuple
import uuid

from bs4 import BeautifulSoup
from mkdocs.config import config_options
from mkdocs.plugins import BasePlugin
import requests
import urllib3

URL_TIMEOUT = 10.0
_URL_BOT_ID = f'Bot {uuid.uuid4()}'
URL_HEADERS = {'User-Agent': _URL_BOT_ID}

urllib3.disable_warnings()


class HtmlProoferPlugin(BasePlugin):

    config_scheme = (
        ('raise_error', config_options.Type(bool, default=False)),
        ('raise_error_excludes', config_options.Type(dict, default={}))
    )

    def on_post_page(self, output_content: str, config, **kwargs):
        soup = BeautifulSoup(output_content, 'html.parser')
        for a in soup.find_all('a', href=True):
            url = a['href']
            clean_url, url_status = self.get_url_status(url, soup)
            if self.bad_url(url_status) is True:
                error = f'{clean_url}: {url_status}\n'
                excludes = self.config['raise_error_excludes']
                if (self.config['raise_error'] and
                    (url_status not in excludes or
                     ('*' not in excludes[url_status] and
                      url not in excludes[url_status]))):
                    raise Exception(error)
                else:
                    print(error)

    @lru_cache(maxsize=500)
    def get_url_status(self, url: str, soup: BeautifulSoup) -> Tuple[str, int]:
        for local in ('localhost', '127.0.0.1', 'app_server'):
            if re.match(f'https?://{local}', url):
                return url, 0
        clean_url = url.strip('?.')
        if url.startswith('#') and not soup.find(id=url.strip('#')):
            return url, 404
        elif re.match('https?://', clean_url):
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
            return url, 0

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
