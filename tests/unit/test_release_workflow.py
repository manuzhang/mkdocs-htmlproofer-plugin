import pathlib
import re


def test_release_actions_are_pinned():
    path = pathlib.Path(__file__).resolve().parents[2] / '.github/workflows/publish.yml'
    actions = re.findall(r'uses:\s*(\S+)', path.read_text())
    assert actions
    assert all(re.fullmatch(r'[^@]+@[0-9a-f]{40}', action) for action in actions)
