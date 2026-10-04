import pytest

from htmlproofer.plugin import HtmlProoferPlugin


@pytest.mark.timeout(5)
def test_deep_rendered_anchors_and_nested_templates():
    depth = 5000
    html = ''.join(f'<div id="a{i}">' for i in range(depth)) + '</div>' * depth
    html += ('<template id="live-template"><a name="hidden"></a>'
             '<template id="hidden-template"><div id="hidden-id"></div></template></template>'
             '<a id="outside" name="legacy"></a>')
    anchors = HtmlProoferPlugin.rendered_anchors(html)
    assert anchors == {f'a{i}' for i in range(depth)} | {'live-template', 'outside', 'legacy'}
