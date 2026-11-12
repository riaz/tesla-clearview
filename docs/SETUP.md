# One-time setup

Tesla requires every Fleet API app, personal ones included, to have a registered domain hosting a public key. The car must also have that key installed as a "virtual key" before it accepts commands.

## 1. Generate keys

```bash
./scripts/gen_keys.sh
```

This writes the following to `config/` (gitignored):
- `fleet-key.pem` / `fleet-public-key.pem`: command-signing key pair (prime256v1). **Keep the private key secret.**
- `tls-key.pem` / `tls-cert.pem`: self-signed TLS cert for the local signing proxy.

## 2. Publish the Pages site (public key + login callback)

clearview runs on your home server, but Tesla needs a public HTTPS domain for two static files:

- `/.well-known/appspecific/com.tesla.3p.public-key.pem`: your public key. Tesla fetches it when you register, and the Tesla app fetches it when you pair the key.
- `/callback/`: the OAuth redirect page ([site/callback/index.html](../site/callback/index.html)). Tesla sends your browser here after login, and the page forwards it to your home server. The home-server address travels in the OAuth `state`, and the page only follows private addresses (localhost, LAN IPs, `*.local`, Tailscale), so nothing at home is exposed to the internet.

Use a GitHub Pages **user site**: a repo named exactly `<user>.github.io`. It has to be the user site because `.well-known` must sit at the root of the domain. Create the repo on GitHub, enable Pages (Settings → Pages → Deploy from branch `main`, root), clone it, then:

```bash
./scripts/publish_site.sh ~/path/to/<user>.github.io
```

Check (it can take a minute or two after the push):

```bash
curl https://<user>.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem
open https://<user>.github.io/callback/
```

## 3. Create the developer app

At <https://developer.tesla.com>, create an application:
- **Allowed origin**: `https://<user>.github.io`
- **Redirect URI**: `https://<user>.github.io/callback/` (include the trailing slash; it must match `.env` exactly)
- **Scopes**: Vehicle Information, Vehicle Location, Vehicle Commands

Fill in `.env`:

```
TESLA_CLIENT_ID=...
TESLA_CLIENT_SECRET=...
TESLA_DOMAIN=<user>.github.io
TESLA_REDIRECT_URI=https://<user>.github.io/callback/
TESLA_REGION=na              # na = North America/APAC, eu = Europe/Middle East/Africa
CLEARVIEW_API_TOKEN=...      # openssl rand -hex 24; this is your dashboard password
API_HOST=0.0.0.0             # so phones/laptops on your LAN can open the dashboard
```

## 4. Start the server, register, and log in

```bash
docker compose up -d          # signing proxy (step 6)
uv run clearview serve        # → http://<home-server>:8787
```

Open the dashboard from any device on your network and sign in with `CLEARVIEW_API_TOKEN`. The **Setup** card checks each step:

1. **Register**: registers your domain with the Fleet API region (once).
2. **Connect**: Tesla login → `https://<user>.github.io/callback/` → back to `http://<home-server>:8787/auth/callback`. Tokens are stored on the home server only (`~/.config/clearview/tokens.json`).

The callback page sends your browser to the URL the dashboard was opened on. If you use a reverse proxy or a different hostname, set `CLEARVIEW_PUBLIC_URL`.

CLI alternative: `uv run clearview auth register` and `uv run clearview auth login`. When the browser runs on the same machine, the callback page bounces to a one-shot listener on `localhost:8765`. On a headless server, use `--no-browser` and paste the URL you end up on.

### Away from home

Install [Tailscale](https://tailscale.com) on the server and your phone and open `http://<server>.<tailnet>.ts.net:8787`. The callback page accepts `*.ts.net` and `100.64.0.0/10` addresses. Traffic on your LAN is plain HTTP, so Tailscale is also the easy way to encrypt it.

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
