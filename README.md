# relay-server

Minimal SIP/WebRTC calling infrastructure for the Relay Flutter app, deployed by Docker Compose on a Linux VPS through Coolify. Two services: FreeSWITCH and coturn. Coolify's existing proxy terminates TLS for SIP over WSS. No application API, external database, SIP router, or monitoring stack.

```text
Relay Flutter app A ── SIP/WSS ──┐
                               FreeSWITCH ── SIP/WSS ── Relay Flutter app B
Relay Flutter app A ── media ───┤          ├── media ─── Relay Flutter app B
                     via coturn when a client's ICE path requires relay
```

FreeSWITCH anchors both media legs; coturn is an optional relay on a client leg. One-to-one calls negotiate **Opus audio** and **VP8 video**, using WebRTC ICE and DTLS-SRTP. Both clients must offer these codecs; there is no PSTN gateway, voicemail, conference, or offline ringing. A recipient must have an active SIP registration.

## Files and startup

- `compose.yaml`: two services, public media mappings, health checks, and FreeSWITCH state volume.
- `freeswitch/Dockerfile`, `modules.conf`, `conf/`: source build and minimal XML configuration. VP8 is provided by FreeSWITCH's core libvpx support; Opus uses `mod_opus`.
- `coturn/Dockerfile`: source build of coturn without external database dependencies.
- `scripts/configure.py`: validates environment, renders XML and TURN configuration at container start, and derives per-extension credentials.
- `scripts/health.py`: local SIP/codec readiness and STUN responsiveness.
- `tests/`: configuration and credential regression checks.

Images use Debian Bookworm and pinned source commits: FreeSWITCH **1.11.3**, coturn **4.18.0**, Sofia-SIP and SpanDSP. Initial builds require outbound access to Debian mirrors and GitHub and may take several minutes. Build parallelism defaults to two jobs. Source revisions are pinned; Debian packages are not frozen to a snapshot. FreeSWITCH uses its embedded SQLite state, persisted in `freeswitch-state`, including registration state and DTLS certificates. Generated configuration is rebuilt on every container restart; logs go to container stdout. Startup fails on missing or invalid credentials and ranges. Neither container runs as root.

## Prepare configuration

```sh
cp .env.example .env
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

Set `SIP_PASSWORD_SEED`, `ESL_PASSWORD`, and `TURN_PASSWORD` to the three **different** generated values. Each must be 64 hex characters. `.env.example` contains no usable credentials; `.env` is ignored by Git and Docker builds. In Coolify, enter these as runtime environment variables rather than committing an `.env` file.

Set:

| Variable | Meaning |
| --- | --- |
| `SIP_DOMAIN` | SIP authentication domain, e.g. `sip.example.com`; lowercase DNS hostname |
| `PUBLIC_IP` | Actual public IPv4 address advertised for signaling and media; replace the documentation example |
| `SIP_EXT_START`, `SIP_EXT_END` | Inclusive numeric user range, default 1001–1099; supports 1–10,000 users, no leading zeroes |
| `SIP_PASSWORD_SEED` | Stable private seed for distinct extension passwords |
| `ESL_PASSWORD` | Administrative event socket password; socket listens only on container loopback |
| `TURN_REALM` | TURN realm and suggested DNS name, e.g. `turn.example.com` |
| `TURN_USERNAME`, `TURN_PASSWORD` | Shared long-term TURN credentials, independent of SIP credentials |
| `RTP_START`, `RTP_END` | FreeSWITCH UDP media range, default 16384–16483; start must be even |
| `TURN_PORT` | STUN/TURN listener, default 3478, UDP and TCP |
| `TURN_MIN_PORT`, `TURN_MAX_PORT` | coturn UDP relay range, default 49160–49259 |

Ranges must be disjoint. Host and container media ports have identical numbers. This deployment supports IPv4 and Docker bridge networking. Behind upstream NAT, forward these same ports one-to-one to the VPS private address and set `PUBLIC_IP` to the public address. Both Docker NAT and upstream NAT must preserve media port numbers. It does not support a VPS behind CGNAT without inbound forwarding. The local container addresses are determined at startup; do not put Docker private addresses into `PUBLIC_IP`. The client ICE ACL accepts public IPv4 candidates, including TURN relay candidates; private-only LAN candidates are deliberately filtered.

## Deploy manually with Coolify

1. Push this repository to your own Git provider. No deployment occurs merely from creating these files.
2. On the Linux VPS, point DNS A records for `sip.example.com` and `turn.example.com` to `PUBLIC_IP`. Use direct DNS, with any CDN proxy disabled. A typical HTTP CDN does not carry the required UDP media traffic.
3. In Coolify, create a resource from that Git repository, choose **Docker Compose**, and select `/compose.yaml` as the Compose location. Use the repository root as build context. Keep Compose's explicit port mappings.
4. Enter all values from `.env.example` into the resource's runtime environment, including the three generated secrets. Do not expose them as build arguments. Use a fresh named FreeSWITCH volume so its initial ownership is set by the image.
5. Set the **freeswitch service domain** to `https://sip.example.com:5066`. The `:5066` tells Coolify the internal backend port; public clients use `wss://sip.example.com` on **443**, without `:5066`. Coolify must route to the backend using plain HTTP/WS, preserve WebSocket upgrade headers, and issue a trusted certificate for `sip.example.com`. Do not assign a Coolify HTTP domain to coturn.
6. Open the direct ports below in the VPS firewall and the provider's security group. Behind NAT, also configure upstream forwarding. Docker-published ports can bypass ordinary UFW rules: enforce restrictions at the provider firewall or Docker's forwarding rules as appropriate.
7. Click **Deploy** when ready. Check build logs and service health, then complete the external verification checklist below. This repository does not trigger deployment, create DNS records, change firewalls, or request certificates itself.

Coolify connects its proxy to the Compose resource network for domain routing. WS port 5066 is only exposed on that network, with no host mapping. Standard SIP port 5060 is not published, and administrative port 8021 remains container-local. See [Coolify networking](https://coolify.io/docs/core/networking-in-coolify) for domain routing versus direct host ports.

| Public VPS ports (defaults) | Handler | Purpose |
| --- | --- | --- |
| TCP 80, 443 | Coolify proxy | Certificate issuance/HTTP redirect and SIP/WSS on 443 |
| UDP 16384–16483 | Direct Compose mapping to FreeSWITCH | ICE checks, DTLS handshake, SRTP/SRTCP audio/video |
| UDP and TCP 3478 | Direct Compose mapping to coturn | STUN and authenticated TURN allocation/client transport |
| UDP 49160–49259 | Direct Compose mapping to coturn | TURN relay sockets exchanging packets with FreeSWITCH |

Coolify's HTTP proxy does **not** forward the media ranges or TURN listener. Registration can work through 443 while calls have no media if UDP is blocked. The RTP range must be exposed because FreeSWITCH allocates media sockets dynamically; audio and video on both call legs consume ports from it. The small default ranges suit initial testing, not a promised call capacity. Increase ranges, firewall rules, and quotas together after measuring load.

For a manual deployment outside Coolify, after configuring `.env`:

```sh
docker compose config --quiet
docker compose build
# Start only when you intend to deploy:
docker compose up -d
```

A standalone Compose start still needs a trusted TLS/WebSocket reverse proxy on the same Docker network, forwarding 443 to `freeswitch:5066`; that proxy is intentionally provided by Coolify in the target deployment.

## Configure the Relay Flutter SIP client

Example values for extension 1001:

```text
WebSocket URL:          wss://sip.example.com
SIP URI:                sip:1001@sip.example.com
Authorization username: 1001
Password:               derived per-extension password (command below)
Destination:            sip:1002@sip.example.com
```

Use a SIP-over-WebSocket client supporting the `sip` WebSocket subprotocol and WebRTC media. Keep the connection alive and refresh REGISTER before its expiry. Configure its WebRTC ICE servers:

```json
[
  {"urls": ["stun:turn.example.com:3478"]},
  {
    "urls": ["turn:turn.example.com:3478?transport=udp", "turn:turn.example.com:3478?transport=tcp"],
    "username": "<TURN_USERNAME>",
    "credential": "<TURN_PASSWORD>"
  }
]
```

STUN binding discovery is unauthenticated; TURN allocations require the long-term credentials. TURN over TCP is supported for the client-to-coturn connection, while coturn-to-FreeSWITCH relay media remains UDP. **TURN TLS (`turns:`) is not enabled**, and restrictive networks allowing only TLS on 443 may still fail. Coolify's WSS certificate does not secure TURN. Adding TURN TLS requires a separate trusted certificate and direct TLS listener, not an HTTP domain route.

Get one extension's credentials on the server:

```sh
docker compose exec freeswitch python3 /opt/relay/configure.py credentials 1001
```

In Coolify's freeswitch container terminal, omit `docker compose exec freeswitch`. The command intentionally displays that user's password: provision it privately. Never put the seed or ESL password in the Flutter app. Changing the seed or domain changes all derived passwords; changing the range adds/removes users on restart. Redeploy after changes, reprovision affected clients, and have them register again. Restarting interrupts active calls. One registration is allowed per extension.

Passwords are derived with HMAC-SHA256 from the secret seed plus extension/domain. The directory stores SIP digest A1 hashes, not plaintext passwords. This is simple static provisioning, with no user-management API. TURN credentials are shared among clients; treat them as exposed client credentials, rotate them as needed, and monitor usage. Allocation quotas and bandwidth limits are set in `scripts/configure.py`.

The dialplan represents the inclusive extension range using compact decimal-prefix patterns, rather than listing each user in a regex. Directory generation still creates one authenticated entry per extension, up to the existing 10,000-user limit. User count is not a concurrent-call capacity guarantee.

## How calling works

- **Signaling** is SIP messages over WSS: register, invite, ring, answer, negotiate SDP, and hang up. **Media** is the actual encrypted audio/video in SRTP packets on separate UDP sockets. WSS encryption alone does not secure or transport media; WebRTC uses DTLS-SRTP.
- **SIP registration** authenticates an extension and associates its SIP identity with a current contact/connection. FreeSWITCH uses that registered contact when bridging a call to `user/extension@domain`. Calls also require SIP authentication; the dialplan only routes configured extensions.
- **ICE** gathers candidate addresses and tests candidate pairs to select a usable client-to-FreeSWITCH media path. FreeSWITCH advertises the configured public IP and accepts public client/relay candidates.
- **STUN** lets a client learn its public address/port as seen through NAT. It helps discover a candidate but does not relay media or guarantee reachability.
- **TURN** allocates an authenticated relay address when a usable direct path is unavailable. The client connects to coturn; coturn exchanges packets with FreeSWITCH. TURN belongs in the client's ICE settings: FreeSWITCH does not request a TURN allocation itself.

The FreeSWITCH [WebRTC/SIP documentation](https://developer.signalwire.com/freeswitch/users-and-endpoints/webrtc-sip/) covers WS bindings, DTLS-SRTP, ICE candidates, and the public RTP address. coturn's [configuration reference](https://github.com/coturn/coturn/blob/master/examples/etc/turnserver.conf) explains port-preserving NAT mapping, credentials, and relay ranges.

## Health and debugging

Run these from the VPS repository checkout, or run their inner commands in the corresponding Coolify container terminal:

```sh
docker compose ps
docker compose logs --tail=100 freeswitch coturn
docker compose exec freeswitch python3 /opt/relay/health.py freeswitch
docker compose exec coturn python3 /opt/relay/health.py coturn

docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "status"'
docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "sofia status profile internal"'
docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "sofia status profile internal reg"'
docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "show codecs"'
docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "show channels"'

# Temporarily trace SIP; disable after testing. Logs contain identities and SDP.
docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "sofia profile internal siptrace on"'
docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "sofia profile internal siptrace off"'

# A self-contained TURN allocation/relay test using coturn's bundled test client.
docker compose exec coturn sh -c 'turnutils_uclient -y -u "$TURN_USERNAME" -w "$TURN_PASSWORD" -p "$TURN_PORT" "$PUBLIC_IP"'
```

The FreeSWITCH health check verifies process responsiveness through loopback ESL, the running internal SIP profile, and Opus/VP8 availability. It does not probe the WS bind address; failures report fixed diagnostics without credential-bearing command arguments or raw subprocess output. The coturn health check verifies a local STUN response. They do **not** verify trusted TLS, authenticated TURN allocations, or external media reachability. The self-contained TURN test is useful but still originates on the VPS. Run an equivalent test from a separate network as well. For TCP client transport, add `-t` to `turnutils_uclient`.

Before considering a deployment ready:

1. Check `docker compose config --quiet`, build both images, and confirm both services are healthy.
2. From outside the VPS, check certificate trust and a successful WSS handshake using the SIP client; an ordinary HTTP GET is not a SIP test.
3. Register two users from the configured range, e.g. 1001 and 1002. Confirm wrong passwords and an out-of-range user cannot register. Confirm unauthenticated INVITEs cannot place calls.
4. Make audio calls in both directions and verify Opus in SDP and two-way sound. Make video calls and verify VP8 and two-way video. Confirm dialing a number outside the range is rejected.
5. Repeat with users on different networks, e.g. Wi-Fi and mobile data. Temporarily force the client's ICE transport policy to `relay`; confirm a relay candidate is selected and two-way audio/video still works. Restore normal ICE policy afterward.
6. Verify incorrect TURN credentials cannot allocate a relay. Test both UDP and TCP TURN transport from an external network.
7. Change the extension range, redeploy, provision a newly included user, and verify a removed user cannot register. Re-register clients after restarting.

For failed media, inspect SDP for `PUBLIC_IP`, `opus`, `VP8`, ICE credentials/candidates, and DTLS fingerprint attributes; inspect the client's selected ICE pair and RTP counters. Check VPS/provider firewall rules, port-preserving mappings, and Docker hairpin NAT when coturn and FreeSWITCH share the VPS. A TURN allocation alone does not prove its relay ports can reach FreeSWITCH.

## Verification and reference provenance

```sh
python3 -m unittest discover -s tests -v
```

Tests cover compact range patterns and rejected destinations (including 10,000-user ranges), sanitized health failures and ESL readiness, distinct/stable credentials and digest hashes, unsafe/missing configuration, TURN NAT mapping, and overlapping ports. Docker image builds and real two-client calls must still be validated on a Docker-enabled Linux host; Docker is unavailable in the authoring workspace, so runtime interoperability is not claimed as tested here.

Before writing this repository, the reference's complete `deploy/freeswitch` tree was inspected at commit [`562968b`](https://github.com/builds-toqyo/telephony-platform/tree/562968b89d8b042b253db0fe1cdcc358e61f3d75/deploy/freeswitch). Relevant patterns retained: Sofia `sip-ip`/`rtp-ip` and external address settings, explicit RTP ranges, XML directory credentials, and local `user/…@domain` bridging. Its checked-in profile has no WS/WSS binding, its module list does not load Opus, and it has demo passwords, public ESL binding, and unrelated routing/features. These were not carried over. Relay adds the missing WebRTC configuration using upstream FreeSWITCH guidance; the reference is not evidence of a tested Relay deployment.

FreeSWITCH, Sofia-SIP, SpanDSP, and coturn retain their upstream licenses in the built images. Source revision links are recorded in the Dockerfiles; no full platform architecture was copied.
