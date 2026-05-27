# Security Policy

## Supported Status

Skydnir is experimental. Security-sensitive behavior changes quickly
while the Android direct executor, syscall mediation, container filesystem
handling, and APK packaging model are being developed.

Use the latest commit on the main branch for testing unless a release note
explicitly marks a build as supported.

## Reporting Vulnerabilities

Do not publish exploit details or private credentials in public issues.

For now, open a GitHub issue with a minimal public description and mark it as a
security-sensitive report. Include:

- affected commit or APK version;
- Android version and device model;
- APK flavor (`compat` or `modern`);
- reproduction steps without real secrets;
- logs with tokens, API keys, passwords, cookies, and local paths redacted.

If a private disclosure channel is added later, this file should be updated
before publishing release builds more broadly.

## Secrets And Signing Material

Never commit:

- GitHub tokens or personal access tokens;
- API keys such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or cloud provider
  credentials;
- code-server passwords;
- Android keystores, certificates, private keys, or signing property files;
- device-local ADB pairing codes or private debug endpoints.

Release signing is configured through environment variables or ignored local
property files. The repository ignores common keystore and certificate file
extensions.

If a secret is printed locally or suspected to have been committed:

1. Revoke the credential immediately.
2. Replace it with a new credential.
3. Run the secret audit procedure in
   [`docs/test/SECRET_AUDIT.md`](docs/test/SECRET_AUDIT.md).
4. If the secret was committed, assume history rewrite or a clean repository
   migration is required. Deleting the latest file is not enough.

## Scope Notes

The app runs inside the Android app sandbox, but it intentionally executes
container-derived userspace code in experimental modes. Treat untrusted images
as untrusted code. Do not expose local service ports or code-server instances
to untrusted networks without authentication.
