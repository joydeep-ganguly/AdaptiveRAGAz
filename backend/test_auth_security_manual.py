"""
Manual sanity check for the auth/security layer (point 6). Run from inside backend/:

    python test_auth_security_manual.py
"""

from app.auth.security import create_access_token, decode_access_token, hash_password, verify_password

hashed = hash_password("supersecret")
print("Hash:", hashed)
print("Verify correct password:", verify_password("supersecret", hashed))
print("Verify wrong password:", verify_password("wrongpass", hashed))

token = create_access_token(subject="alice")
print("Token:", token)
print("Decoded subject:", decode_access_token(token))
print("Decoded garbage token:", decode_access_token("not-a-real-token"))