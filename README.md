# mkdocs-htmlproofer-plugin

*A MkDocs plugin that checks URL*



## Installation

> **Note:** This package requires MkDocs version 0.17 or higher. 

Install the package with pip:

```bash
pip install mkdocs-htmlproofer-plugin
```

Enable the plugin in your `mkdocs.yml`:

```yaml
plugins:
    - search
    - htmlproofer
```

> **Note:** If you have no `plugins` entry in your config file yet, you'll likely also want to add the `search` plugin. MkDocs enables it by default if there is no `plugins` entry set, but now you have to enable it explicitly.

More information about plugins in the [MkDocs documentation][mkdocs-plugins]

