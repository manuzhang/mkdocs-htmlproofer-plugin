from mkdocs.plugins import BasePlugin

from bs4 import BeautifulSoup
import urllib.request


class HtmlProoferPlugin(BasePlugin):

    def on_post_page(self, output_content, config, **kwargs):
        soup = BeautifulSoup(output_content, 'html.parser')
        local = ['localhost', '127.0.0.1']
        for a in soup.find_all('a', href=True):
            url = a['href']
            for local in ('localhost', '127.0.0.1', 'app_server'):
                if url.startswith('http://' + local):
                    return
            if url.startswith('http'):
                print('Checking url ' + url)
                try:
                    status = urllib.request.urlopen(url, timeout=10).getcode()
                    if status != 200:
                        print('Bad url ' + url)
                except:
                    print('Failed to open url ' + url)



