from unittest.mock import patch

from tempera.observe.gateway import create_app


def test_enforced_gateway_without_registry_rejects_direct_bypass():
    app = create_app("http://target.test", "run-1", "agent", lambda event: None,
                     enforce_policy=True)
    with patch("tempera.observe.gateway.requests.request") as request:
        assert app.test_client().get("/").status_code == 403
    assert request.call_count == 0
