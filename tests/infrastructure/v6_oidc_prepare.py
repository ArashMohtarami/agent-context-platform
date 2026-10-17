"""Generate synthetic disposable Keycloak realms, TLS and secrets; no real emails."""

import ipaddress
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyotp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

root = Path(os.environ["V6_TEST_DIR"]) / "oidc"
root.mkdir(mode=0o755)
(root / "realms").mkdir(mode=0o755)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable V6 provider")])
now = datetime.now(UTC)
certificate = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(minutes=5))
    .not_valid_after(now + timedelta(days=1))
    .add_extension(
        x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
    )
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
    .sign(key, hashes.SHA256())
)
(root / "server.crt").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
(root / "server.key").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
)
(root / "admin").write_text(secrets.token_urlsafe(32))
(root / "start.sh").write_text(
    '#!/bin/bash\nset -eu\nexport KC_BOOTSTRAP_ADMIN_PASSWORD="$(cat /fixture/admin)"\n'
    "exec /opt/keycloak/bin/kc.sh start-dev --http-enabled=false --import-realm\n"
)
origin = os.environ.get("V6_BROWSER_TEST_ORIGIN", "https://identity.example")
provider_origin = "https://127.0.0.1:" + os.environ["V6_OIDC_PORT"]
registrations = []
for suffix in ("a", "b"):
    realm = "v6-org-" + suffix
    secret = secrets.token_urlsafe(48)
    issuer = provider_origin + "/realms/" + realm
    endpoint = issuer + "/protocol/openid-connect/"
    registrations.append(
        {
            "registration_id": "provider-" + suffix,
            "org_id": "org-" + suffix,
            "issuer": issuer,
            "client_id": "acp-" + suffix,
            "client_secret": secret,
            "redirect_uri": origin + "/api/v6/auth/oidc/callback",
            "post_logout_redirect_uri": origin + "/api/v6/auth/oidc/logged-out",
            "authorization_endpoint": endpoint + "auth",
            "token_endpoint": endpoint + "token",
            "jwks_uri": endpoint + "certs",
            "end_session_endpoint": endpoint + "logout",
            "connect_ip": "127.0.0.1",
            "ca_file": str(root / "server.crt"),
        }
    )
    (root / "realms" / (realm + ".json")).write_text(
        json.dumps(
            {
                "realm": realm,
                "enabled": True,
                "registrationAllowed": False,
                "resetPasswordAllowed": False,
                "sslRequired": "all",
                "accessTokenLifespan": 300,
                "clients": [
                    {
                        "clientId": "acp-" + suffix,
                        "enabled": True,
                        "publicClient": False,
                        "secret": secret,
                        "standardFlowEnabled": True,
                        "directAccessGrantsEnabled": False,
                        "redirectUris": [origin + "/api/v6/auth/oidc/callback"],
                        "attributes": {
                            "pkce.code.challenge.method": "S256",
                            "post.logout.redirect.uris": origin + "/api/v6/auth/oidc/logged-out",
                        },
                        "protocol": "openid-connect",
                        "defaultClientScopes": ["basic", "acr", "profile", "email", "roles"],
                        "fullScopeAllowed": False,
                    }
                ],
                "users": [
                    {
                        "id": "oidc-alice-" + suffix,
                        "username": "alice",
                        "enabled": True,
                        "email": "alice@example.test",
                        "emailVerified": True,
                        "firstName": "Synthetic",
                        "lastName": "Alice",
                        "credentials": [
                            {
                                "type": "password",
                                "value": "disposable-provider-password",
                                "temporary": False,
                            }
                        ],
                    },
                    {
                        "id": "oidc-other-" + suffix,
                        "username": "other",
                        "enabled": True,
                        "email": "other@example.test",
                        "emailVerified": True,
                        "firstName": "Synthetic",
                        "lastName": "Other",
                        "credentials": [
                            {
                                "type": "password",
                                "value": "disposable-provider-password",
                                "temporary": False,
                            }
                        ],
                    },
                ],
            }
        )
    )
# Realm B proves trusted MFA using real required OTP executions and Keycloak's
# native AMR mapper. Realm A intentionally provides no trusted MFA assurance.
otp = pyotp.random_base32()
(root / "totp").write_text(otp)
path = root / "realms" / "v6-org-b.json"
realm_b = json.loads(path.read_text())
realm_b["browserFlow"] = "acp-two-factor"
realm_b["authenticationFlows"] = [
    {
        "alias": "acp-two-factor",
        "providerId": "basic-flow",
        "topLevel": True,
        "builtIn": False,
        "authenticationExecutions": [
            {
                "authenticator": "auth-username-password-form",
                "authenticatorConfig": "pwd-reference",
                "requirement": "REQUIRED",
                "priority": 10,
                "authenticatorFlow": False,
                "userSetupAllowed": False,
            },
            {
                "authenticator": "auth-otp-form",
                "authenticatorConfig": "otp-reference",
                "requirement": "REQUIRED",
                "priority": 20,
                "authenticatorFlow": False,
                "userSetupAllowed": False,
            },
        ],
    }
]
realm_b["authenticatorConfig"] = [
    {
        "alias": method + "-reference",
        "config": {
            "default.reference.value": method,
            "default.reference.maxAge": "600",
        },
    }
    for method in ("pwd", "otp")
]
realm_b["clients"][0]["protocolMappers"] = [
    {
        "name": "verified-authentication-methods",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-amr-mapper",
        "consentRequired": False,
        "config": {"id.token.claim": "true", "access.token.claim": "false"},
    }
]
for user in realm_b["users"]:
    user["credentials"].append(
        {
            "type": "otp",
            "userLabel": "disposable-test-factor",
            "secretData": json.dumps({"value": otp}),
            "credentialData": json.dumps(
                {
                    "subType": "totp",
                    "digits": 6,
                    "counter": 0,
                    "period": 30,
                    "algorithm": "HmacSHA1",
                    "secretEncoding": "BASE32",
                }
            ),
        }
    )
path.write_text(json.dumps(realm_b))
registrations[1].update({"mfa_amr": ["pwd", "otp"], "require_mfa": True})
(root / "registrations.json").write_text(json.dumps(registrations))
for path in root.rglob("*"):
    if path.is_file():
        path.chmod(0o444)  # parent temp directory 0700; container reads only its bind mount.
