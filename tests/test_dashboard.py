def test_dashboard_served_with_strict_csp(client):
    r = client.get("/app/")
    assert r.status_code == 200 and "ABBIS-Lite" in r.text
    assert "<script>" not in r.text                      # no inline scripts, so the strict CSP works
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/app/app.js").status_code == 200
    assert client.get("/app/style.css").status_code == 200


def test_root_redirects_to_dashboard(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
