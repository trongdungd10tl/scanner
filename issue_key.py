"""
Issues an API key for one caller. Prints the key to hand over and the entry to
add to API_KEYS. The key is not written anywhere, so keep the output.

    python issue_key.py cms
    python issue_key.py partnerA
"""
import hashlib
import secrets
import sys

if len(sys.argv) != 2 or not sys.argv[1].strip():
    print(__doc__)
    sys.exit(1)

name = sys.argv[1].strip()
key = f"scanner_{name}_{secrets.token_urlsafe(24)}"
digest = hashlib.sha256(key.encode("utf-8")).hexdigest()

print()
print("Give this to the caller. It is stored nowhere and cannot be recovered:")
print(f"  {key}")
print()
print("Add this to API_KEYS in docker-compose.yml, comma separated:")
print(f"  {name}:{digest}")
print()
