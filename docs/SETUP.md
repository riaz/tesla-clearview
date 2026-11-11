# One-time setup

Tesla requires every Fleet API app, personal ones included, to have a registered domain hosting a public key. The car must also have that key installed as a "virtual key" before it accepts commands.

## 1. Generate keys

```bash
./scripts/gen_keys.sh
```

This writes the following to `config/` (gitignored):
- `fleet-key.pem` / `fleet-public-key.pem`: command-signing key pair (prime256v1). **Keep the private key secret.**
- `tls-key.pem` / `tls-cert.pem`: self-signed TLS cert for the local signing proxy.

## 2. Host the public key

Tesla fetches `https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem`.

Easiest free option is GitHub Pages on your user site repo (`<user>.github.io`):

```bash
mkdir -p .well-known/appspecific
cp config/fleet-public-key.pem .well-known/appspecific/com.tesla.3p.public-key.pem
touch .nojekyll          # without this, Pages won't serve dot-folders
git add -A && git commit -m "Tesla public key" && git push
```

Check: `curl https://<user>.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem`

## 3. Create the developer app

At <https://developer.tesla.com>, create an application:
- **Allowed origin**: `https://<user>.github.io`
- **Redirect URI**: `http://localhost:8765/callback`
- **Scopes**: Vehicle Information, Vehicle Location, Vehicle Commands

Put the client ID and secret into `.env`, and set `TESLA_DOMAIN=<user>.github.io` and `TESLA_REGION` (`na` for North America/APAC, `eu` for Europe/Middle East/Africa).

## 4. Register and log in

```bash
uv run clearview auth register   # registers your domain with the region (once)
uv run clearview auth login      # browser login to your Tesla account
uv run clearview vehicles
```

## 5. Pair the virtual key with the car

```bash
uv run clearview auth pair-url   # → https://tesla.com/_ak/<domain>
```

Open that link **on your phone** (Tesla app installed), and approve with your key card in the car.

## 6. Run the signing proxy

Modern Teslas only accept commands signed with your key. Tesla's `vehicle-command` proxy does the signing:

```bash
docker compose up -d
```

`.env` defaults `TESLA_COMMAND_PROXY_URL=https://localhost:4443` and trusts `config/tls-cert.pem`.

## 7. Cameras (optional)

The Fleet API can't return camera images (see README). To enable the vision check, set `TESLACAM_PATH` to a folder containing `TeslaCam/` clips:

- **Hands-off:** a Raspberry Pi Zero 2 W in the car as the USB drive ([TeslaUSB](https://github.com/marcone/teslausb)). It syncs clips to your home NAS/Mac over Wi-Fi, so it only works when the car is parked within Wi-Fi range.
- **Manual:** plug in the USB stick and run `clearview analyze /Volumes/<drive>/TeslaCam`.

Clips older than `CLIP_MAX_AGE_MIN` are ignored, so a stale clip never decides today's defrost. Turn Sentry Mode on when you park to get fresh SentryClips.

## 8. Notifications

Install the **ntfy** app and subscribe to a long, random topic name. Put the same name in `NTFY_TOPIC`. Anyone who knows the topic can read it, so make it unguessable, or self-host ntfy with a token.
