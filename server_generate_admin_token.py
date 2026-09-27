import hashlib
import secrets


token = secrets.token_urlsafe(32)
digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
print("Administrator token (save this somewhere private):")
print(token)
print("\nPut this line in .env:")
print("OVERMOD_ADMIN_TOKEN_SHA256=" + digest)

