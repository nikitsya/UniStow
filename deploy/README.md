# UniStow deployment

This configuration runs FastAPI, PostgreSQL 16 and Caddy on a dedicated Ubuntu EC2
instance. Only Caddy publishes ports. PostgreSQL remains on an internal Docker
network. Caddy obtains and renews HTTPS certificates automatically.

## Server and DNS

The initial deployment on 4 November 2026 uses instance `i-0455c51903253118b`
in `eu-north-1`, Elastic IP `13.61.161.1`, and `/opt/unistow` as the application
directory. The public URL is https://unistow.app; `www` redirects to this address.
Both DNS records use DNS-only mode. Caddy has obtained Let's Encrypt certificates.

The deployment has a fresh database. Registration, sign-in, Secure/HttpOnly session
cookies, public pages, the locker API and HTTPS redirects were verified. The
temporary verification account was removed afterwards. Stripe credentials are
not yet configured, and the physical controller has not been switched to this
server. A new device key is stored in the server's private `.env.production` file.

Use Ubuntu 24.04 LTS, at least 2 GiB RAM and 20 GiB storage. Allow TCP ports 80 and
443 publicly and restrict SSH (22) to your current IP. Do not expose 5432 or 8000.
Keep the instance metadata service on IMDSv2. Install Docker Engine and the Compose
plugin using Docker's official Ubuntu instructions.

In Cloudflare, point the apex A record (`@`) to the server's public IPv4 address and
add a `www` CNAME pointing to `unistow.app`. Start with DNS-only records so Caddy can
obtain certificates directly. If proxying is enabled later, use Full (strict) TLS.
An automatically assigned EC2 IPv4 address can change after a stop/start; use an
Elastic IP for a stable address, accounting for AWS IPv4 charges.

## Configuration and first start

Copy the project to `/opt/unistow`. Create a private `.env.production` file there
with permissions `600` containing the following variables. Generate a separate,
strong database password. Never commit this file or put secrets in EC2 user data.

```dotenv
POSTGRES_PASSWORD=
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
UNISTOW_DEVICE_KEY=
```

Stripe must use sandbox keys. The device key must match the physical controller.
The webhook signing secret must belong to the deployed endpoint, not Stripe CLI.

```sh
sudo docker compose --env-file .env.production -f compose.production.yaml -p unistow up -d --build
sudo docker compose --env-file .env.production -f compose.production.yaml -p unistow ps
```

On an empty volume, PostgreSQL creates the schema and nine locker records. Only
Locker 4 is enabled. Existing local accounts and rentals are not copied by these
commands. Do not connect the physical controller to two backends simultaneously.

Register `https://unistow.app/api/stripe/webhook` in the Stripe sandbox with events
`checkout.session.completed`, `checkout.session.expired`,
`checkout.session.async_payment_succeeded` and
`checkout.session.async_payment_failed`. Save its signing secret and recreate the
app container. Set the Pi's `server_url` to `https://unistow.app` when ready to
switch control to the deployed app; retain certificate verification.

## Checks

Verify HTTPS, registration, sign-in, Secure/HttpOnly session cookies, locker
availability and Stripe sandbox payment confirmation. Check hardware heartbeat,
opening and collection in person before demonstrating physical access.

The app accepts forwarded headers because its port is accessible only within the
private Docker networks. Do not publish the app port directly.

## Backups and updates

Before an update, take a database backup and copy it to a separate secure location:

```sh
umask 077
sudo docker compose --env-file .env.production -f compose.production.yaml -p unistow exec -T db pg_dump -U unistow -d unistow -Fc > unistow-backup.dump
```

Test restoration into a separate database. The PostgreSQL volume survives container
recreation, but it is not a backup and does not protect against disk loss.
Never use `docker compose down -v` on this deployment.

After updating the source, rerun the build/start command. Database schema changes
require explicit migrations; initialisation scripts run only on an empty volume.

References: [Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Caddy HTTPS](https://caddyserver.com/docs/automatic-https), and
[Stripe webhooks](https://docs.stripe.com/webhooks).

## Renew Locker 4 PubNub tokens

With physical opening enabled, set `PUBNUB_SECRET_KEY` in the server's private
`.env.production` and recreate the app container. Use the same Testing keyset
as the publish and subscribe keys. The server issues separate one-hour tokens
for the device and the reader and refreshes its subscription after 45 minutes.

Install `renew_token.py` beside `run_locker.py` on the Pi. Set
`pubnub.renew_token` to `true` in its private device configuration and restart
`uniflow-locker.service`. The Pi uses its existing device key and HTTPS server
address to obtain a token at startup and renew it before expiry. Check for
`Renewed the Locker 4 device token.` in the service journal.
