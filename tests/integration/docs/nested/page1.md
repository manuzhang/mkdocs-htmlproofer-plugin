# First Nested Test Page

This plugin checks all our links,
you can either link to a local file without or with an anchor.

## Without anchor

* [Main Page](../index.md)
* [Sub-Page](./page2.md)
* <figure markdown>
  <a href="../assets/hello-world.drawio.svg">
    ![Image](../assets/hello-world.drawio.svg)
  </a>
</figure>

## With anchor

This plugin can detect invalid anchor links to another page, such as
[Acknowledgement](../index.md#BAD_ANCHOR)
or to a nested page
[Invalid Anchor](./page2.md#BAD_ANCHOR).

But allows valid anchors such as
[Main Page](../index.md#mkdocs-htmlproofer-plugin) and
[Table of Contents](../index.md#table-of-contents).

## Image Link absolute/relative

<a href="../assets/hello-world.drawio.svg">![test](../assets/hello-world.drawio.svg)</a>

<a href="/assets/hello-world.drawio.svg">![test](/assets/hello-world.drawio.svg)</a>
