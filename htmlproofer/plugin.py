from mkdocs.plugins import BasePlugin

from bs4 import BeautifulSoup
import re
import requests
import urllib3
import uuid


URL_TIMEOUT = 10.0
_URL_BOT_ID = 'Bot {id}'.format(id=str(uuid.uuid4()))
URL_HEADERS = {'User-Agent': _URL_BOT_ID}

urllib3.disable_warnings()

class HtmlProoferPlugin(BasePlugin):

    def on_post_page(self, output_content, config, **kwargs):
        soup = BeautifulSoup(output_content, 'html.parser')
        local = ['localhost', '127.0.0.1']
        for a in soup.find_all('a', href=True):
            url = a['href']
            clean_url, url_status = self.get_url_status(url)
            if self.bad_url(url_status) is True:
                print('{}: {}\n'.format(clean_url, url_status))



    def get_url_status(self, url):
        for local in ('localhost', '127.0.0.1', 'app_server'):
            if url.startswith('http://' + local):
                return (url, 0)
        clean_url = url.strip('?.')
        if re.match('https?://', clean_url):
            try:
                response = requests.get(
                    clean_url, verify=False, timeout=URL_TIMEOUT,
                    headers=URL_HEADERS)
                return (clean_url, response.status_code)
            except requests.exceptions.Timeout:
                return (clean_url, 504)
            except requests.exceptions.ConnectionError:
                return (clean_url, -1)
        else:
            return (url, 0)


    def bad_url(self, url_status):
        if url_status == -1:
            return True
        elif url_status == 401 or url_status == 403:
            return False
        elif url_status == 503:
            return False
        elif url_status >= 400:
            return True
        return False