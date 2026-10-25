import json
import urllib.parse
import urllib.request


def login(base, username, creds_file="demo_credentials.json"):
    with open(creds_file) as f:
        password = json.load(f)[username]
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(base + "/auth/login", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]
