# Security Policy

DeskPanel is designed as a **trusted local-network** tool, not an internet-facing service.

## Threat model and limitations

- The web interface can trigger **PC-side commands** configured by the owner. Possession of a valid access key plus network access can result in execution of those configured actions.
- The server listens on all IPv4 interfaces by default, so **Windows Firewall rules** are essential. Allow your trusted Private network (or a specific trusted phone/device) only. Never forward the port from your router.
- Requests use **unencrypted HTTP**. The bearer key is included in the dashboard URL and subsequent API requests. Someone able to intercept trusted-network traffic could potentially steal it. Do not use public/untrusted Wi-Fi or expose this server through an unprotected tunnel.
- Origin checking is a defense-in-depth control, **not** authentication. Do not rely on it to prevent an attacker with your key from taking actions.
- The `command` action uses a Windows shell for commands **defined locally by the PC owner**. Use `exec` with an explicit executable/arguments for new shortcuts. Never paste or import untrusted command configuration.
- Generated credentials live in `config.json` in the installation directory. It is intentionally excluded from Git. Protect and rotate your key if your device or repository leaks it.
- The audio output feature uses a Windows interface that can vary between Windows versions. It is not a security boundary.

## Deployment checklist

1. Keep the access key private; never commit `config.json`.
2. Use **Private-only** Windows Firewall rules and keep your router's port forwarding disabled.
3. Run as a standard (non-administrator) Windows account.
4. Keep Windows, Python, and dependencies updated.
5. Use your own trusted Wi-Fi and rotate any accidentally shared key.
6. Review public screenshots for sensitive hostnames, device names, and URLs.

## Reporting an issue

Do not publish exploit details containing other people's personal data or secrets. Before establishing a public security contact, use a private GitHub Security Advisory on the repository if enabled. No dedicated security mailbox is configured in this preview.
