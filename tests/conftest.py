from __future__ import annotations

import pytest
import requests

from fakes import REPO, WORKFLOW, FakeGitHub


@pytest.fixture
def github(monkeypatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(requests, "post", fake.post)
    monkeypatch.setattr(requests, "get", fake.get)
    return fake


@pytest.fixture
def cfg():
    from github_dispatch import DispatchConfig

    return DispatchConfig(token="ghp_test_token", repo=REPO, workflow=WORKFLOW, ref="main")
