from urllib.parse import urlparse
import sys

urls = [
    "postgresql://user:pass[word]@localhost:5432/my-db",
    "postgresql://user:pass-word@localhost:5432/my-db",
    "postgresql://user:pass@my-host-name:5432/my-db",
    "postgresql://user:pass@localhost:5432/my-db-with-hyphen",
]

for url in urls:
    try:
        p = urlparse(url)
        print(f"URL: {url}")
        print(f"  Scheme: {p.scheme}")
        print(f"  Netloc: {p.netloc}")
        print(f"  Path: {p.path}")
        print(f"  Username: {p.username}")
        print(f"  Password: {p.password}")
        print(f"  Hostname: {p.hostname}")
        print(f"  Port: {p.port}")
    except Exception as e:
        print(f"URL: {url} -> ERROR: {e}")
