# relay-server

Minimal, reproducible SIP and WebRTC infrastructure designed as the companion backend for the **Relay Flutter application**, built to demonstrate real-device audio calling, video calling, and screen sharing during the conference presentation:

> **Demystifying WebRTC in Flutter: VoIP and Screen Sharing**  
> *Droidcon Kenya × FlutterCon Kenya, November 2026*

Relay Server provides lightweight, self-hosted VoIP signaling and media anchoring without commercial PBX bloat, external relational databases, or complex billing stacks. The architecture consists of two containerized services orchestrated by Docker Compose: **FreeSWITCH** and **coturn**.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                       RELAY SERVER                                     │
│                                                                                        │
│   Relay Flutter Client A                                      Relay Flutter Client B   │
│   (e.g., Extension 1001)                                      (e.g., Extension 1002)   │
│            │                                                           │               │
│            │ 1. SIP over WSS (Signaling on TCP 443 -> TCP 7443)        │               │
│            ├──────────────────────────┐     ┌──────────────────────────┤               │
│            │                          ▼     ▼                          │               │
│            │               ┌───────────────────────┐                   │               │
│            │               │      FreeSWITCH       │                   │               │
│            │               │    (B2BUA Anchor)     │                   │               │
│            │               └───────────┬───────────┘                   │               │
│            │                           │                               │               │
│            │ 2. DTLS-SRTP Audio/Video  │ 2. DTLS-SRTP Audio/Video      │               │
│            │    (UDP 16384–16483)      │    (UDP 16384–16483)          │               │
│            ▼                           ▼                               ▼               │
│      [ Direct WebRTC Media Path across FreeSWITCH Core ] ──────────────┘               │
│            ▲                                                           ▲               │
│            │ 3. Relayed Media via coturn (When Symmetric NAT Requires) │               │
│            └──────────────────────────┐     ┌──────────────────────────┘               │
│                                       ▼     ▼                                          │
│                            ┌───────────────────────┐                                   │
│                            │        coturn         │                                   │
│                            │    STUN/TURN Relay    │                                   │
│                            │   (UDP/TCP 3478 &     │                                   │
│                            │   UDP 49160–49259)    │                                   │
│                            └───────────────────────┘                                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture and Service Inventory

The server handles two independent SIP call legs and anchors media to bridge incoming and outgoing WebRTC sessions.

### 1. FreeSWITCH (Signaling B2BUA and Media Anchor)
- **Role**: Operates as a Back-to-Back User Agent (B2BUA). It authenticates client registrations, terminates SIP signaling over secure WebSockets (WSS), negotiates SDP, terminates DTLS-SRTP encryption, and bridges media between endpoints (`user/${destination_number}@$${domain}`).
- **Signaling Transport**: Native SIP over WSS listening on port `7443` inside the container. Plain WS on port `5066` is available internally. Standard UDP/TCP SIP port `5060` is not published.
- **Media Transport**: WebRTC DTLS-SRTP audio and video over UDP ports `16384–16483` (configurable via `RTP_START` and `RTP_END`). Both legs terminate media on FreeSWITCH, providing media anchoring and NAT isolation.
- **Supported Codecs**: **Opus** (audio via `mod_opus`) and **VP8** (video via core `libvpx`). Both clients must support these codecs. There is no transcoding to legacy PSTN codecs (G.711, G.729) or H.264.
- **Administrative Interface**: Event Socket Library (ESL) listening strictly on container loopback `127.0.0.1:8021`, protected by `ESL_PASSWORD`.

### 2. coturn (STUN/TURN Services)
- **Role**: Facilitates NAT traversal for clients behind restrictive or symmetric firewalls where direct peer-to-server ICE connectivity fails.
- **Listeners**: STUN/TURN on `UDP 3478` and `TCP 3478`.
- **Relay Range**: UDP relay allocation sockets on `UDP 49160–49259` (configurable via `TURN_MIN_PORT` and `TURN_MAX_PORT`).
- **Authentication**: Long-term credentials mechanism (`lt-cred-mech`) with static shared credentials (`TURN_USERNAME` / `TURN_PASSWORD`).
- **Security Protections**: Denies relay allocation to loopback, broadcast, private RFC 1918, and CGNAT subnets to prevent internal network scanning.

---

## Essential Dialplan & Call Behavior

The dialplan in `freeswitch/conf/dialplan/relay.xml` configures call routing, session parameters, and codec handling for the Flutter application:

```xml
<extension name="extension-to-extension">
  <condition field="destination_number" expression="^(@@EXTENSION_RANGE_PATTERN@@)$">
    <action application="set" data="hangup_after_bridge=true"/>
    <action application="set" data="call_timeout=30"/>
    <!-- The receiving leg must offer WebRTC media, even before its answer. -->
    <action application="export" data="nolocal:media_webrtc=true"/>
    <action application="export" data="nolocal:rtp_secure_media=true"/>
    <!-- Preserve initial call intent independently of dormant video in SDP. -->
    <action application="set" data="sip_copy_custom_headers=false"/>
    <action application="export" data="nolocal:sip_h_X-Relay-Call-Mode=${sip_h_X-Relay-Call-Mode}"/>
    <action application="export" data="rtp_pass_codecs_on_stream_change=true"/>
    <action application="bridge" data="user/${destination_number}@$${domain}"/>
  </condition>
</extension>
```

### Key Dialplan Mechanisms:
1. **Preserving Call Intent (`X-Relay-Call-Mode`)**:
   - WebRTC clients in mobile applications typically negotiate both audio and video lines during initial INVITE or offer dormant video tracks (`m=video ... a=inactive`) to allow seamless mid-call escalation.
   - To distinguish an audio-only call from an intentional video call or screen-sharing session before the user answers, the initiating Flutter client sends a custom SIP header: `X-Relay-Call-Mode: audio | video | screen_share`.
   - The dialplan explicitly exports this header across the B2BUA bridge (`nolocal:sip_h_X-Relay-Call-Mode=${sip_h_X-Relay-Call-Mode}`) to the callee's incoming INVITE, allowing the receiving UI to present the correct incoming call type.
2. **Codec Passing on Stream Modification (`rtp_pass_codecs_on_stream_change=true`)**:
   - When a call transitions mid-session (e.g., upgrading from voice to video or starting an Android screen capture session), a SIP re-INVITE renegotiates the SDP media streams.
   - Setting `rtp_pass_codecs_on_stream_change=true` instructs FreeSWITCH's core to pass codec parameters across the bridge during stream state changes without stalling or resetting the active media channel.
3. **WebRTC Leg Enforcements**:
   - `nolocal:media_webrtc=true` and `nolocal:rtp_secure_media=true` instruct FreeSWITCH's Sofia endpoint to generate a WebRTC-compliant SDP offer (DTLS fingerprint, ICE candidates, RTCP-mux, and SAVPF profile) on the outbound leg to the callee prior to call answer.
4. **Static Configuration vs. Real-Device Media Flow**:
   - The FreeSWITCH XML configuration specifies WebRTC SDP parameters, DTLS-SRTP key negotiation, and RTP media packet forwarding between bridged legs.
   - Actual end-to-end media transmission, video rendering, codec bitrates, and packet loss tolerance depend on physical network routing, firewall configurations, and client-side device capabilities (such as Android `MediaProjection` permissions, camera hardware rotation, and foreground service lifecycles).

---

## Repository Files

- `compose.yaml`: Deployment definition tailored for Coolify VPS environments with Traefik TCP passthrough.
- `compose.standalone.yaml`: Standalone deployment definition for standard Docker Compose hosts outside Coolify.
- `freeswitch/Dockerfile`, `modules.conf`, `conf/`: Multi-stage source build of FreeSWITCH 1.11.3 (Debian Bookworm) and minimal XML configuration.
- `coturn/Dockerfile`, `coturn/entrypoint.sh`: Source build of coturn 4.18.0 without unnecessary external database dependencies.
- `scripts/configure.py`: Validates environment settings, renders XML and coturn configs at boot, generates compact range patterns, and derives deterministic per-extension credentials.
- `scripts/health.py`: Container health check probes for loopback ESL status and UDP STUN binding responses.
- `tests/test_configure.py`, `tests/test_health.py`, `tests/test_compose.py`: Unit regression tests for credential derivation, compact range generation, port disjointness, error sanitization, and Compose structure.

---

## Configuration Reference

Copy `.env.example` to `.env` and configure your environment:

```sh
cp .env.example .env
```

Generate 64-character hexadecimal secrets using OpenSSL:

```sh
openssl rand -hex 32  # For SIP_PASSWORD_SEED
openssl rand -hex 32  # For ESL_PASSWORD
openssl rand -hex 32  # For TURN_PASSWORD
```

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `SIP_DOMAIN` | String | `sip.example.com` | Primary SIP authentication domain and WSS SNI hostname. Must be a lowercase DNS name. |
| `PUBLIC_IP` | IPv4 | `203.0.113.10` | The actual public IPv4 address advertised in SIP SDP and TURN allocations. |
| `SIP_EXT_START` | Integer | `1001` | First inclusive extension number (1 to 999999999, no leading zeroes). |
| `SIP_EXT_END` | Integer | `1099` | Last inclusive extension number. Total range capacity is capped at 10,000 extensions. |
| `SIP_PASSWORD_SEED` | Hex (64 chars) | *None (Required)* | Stable master secret used to derive individual SIP extension passwords via HMAC-SHA256. |
| `ESL_PASSWORD` | Hex (64 chars) | *None (Required)* | Password for internal FreeSWITCH Event Socket CLI commands on `127.0.0.1:8021`. |
| `TURN_REALM` | String | `turn.example.com` | Realm reported by coturn during digest authentication challenges. |
| `TURN_USERNAME` | String | `relay` | Username for STUN/TURN allocations. |
| `TURN_PASSWORD` | Hex (64 chars) | *None (Required)* | Shared password for STUN/TURN allocations. |
| `RTP_START` | Even Integer | `16384` | Beginning of the FreeSWITCH UDP media port range. Must be an even number. |
| `RTP_END` | Integer | `16483` | End of the FreeSWITCH UDP media port range (minimum 4 ports). |
| `TURN_PORT` | Integer | `3478` | Listening port for coturn STUN/TURN requests (both UDP and TCP). |
| `TURN_MIN_PORT` | Integer | `49160` | Lower bound of coturn UDP relay allocation port range. |
| `TURN_MAX_PORT` | Integer | `49259` | Upper bound of coturn UDP relay allocation port range. |
| `WSS_PORT` *(Standalone)* | Integer | `7443` | Standalone host port mapped to FreeSWITCH native WSS (set to `443` if port 443 is free). |
| `WSS_CERT_DIR` *(Standalone)* | Directory Path | `./certs/wss` | Directory on the host containing `wss.pem` for standalone Docker Compose. |

> [!IMPORTANT]
> The media ranges (`RTP_START`–`RTP_END`), the TURN listener (`TURN_PORT`), and the TURN relay range (`TURN_MIN_PORT`–`TURN_MAX_PORT`) **must be completely disjoint**. `scripts/configure.py` verifies this at startup.

---

## Deployment Models

Relay Server supports two deployment approaches: **Standalone Docker Compose** and **Coolify VPS Deployment**.

```text
STANDALONE DEPLOYMENT (compose.standalone.yaml)
Internet Client ──── WSS (TCP 7443 or 443) ────────> FreeSWITCH (Container :7443, Terminates TLS)
Internet Client ──── Media (UDP 16384–16483) ─────> FreeSWITCH (Direct UDP)
Internet Client ──── STUN/TURN (UDP/TCP 3478) ────> coturn (Direct)
Internet Client ──── Relays (UDP 49160–49259) ────> coturn (Direct UDP)

COOLIFY VPS DEPLOYMENT (compose.yaml)
Internet Client ──── WSS (TCP 443) ───────────────> Coolify Traefik (TCP HostSNI Passthrough)
                                                    └──── Raw TLS ────> FreeSWITCH (:7443)
Internet Client ──── Media (UDP 16384–16483) ─────> FreeSWITCH (Direct Compose Mapping)
Internet Client ──── STUN/TURN (UDP/TCP 3478) ────> coturn (Direct Compose Mapping)
Internet Client ──── Relays (UDP 49160–49259) ────> coturn (Direct Compose Mapping)
```

### Approach 1: Standalone Deployment (`compose.standalone.yaml`)
Use this method when deploying on any standard Linux server without Coolify:

1. **DNS Preparation**: Point DNS A records for `SIP_DOMAIN` (e.g., `sip.example.com`) and `TURN_REALM` (e.g., `turn.example.com`) directly to `PUBLIC_IP`. Disable HTTP CDN proxies (Cloudflare orange cloud), as CDNs do not forward arbitrary TCP/UDP media traffic.
2. **Environment File**:
   ```sh
   cp .env.example .env
   # Populate SIP_PASSWORD_SEED, ESL_PASSWORD, TURN_PASSWORD, SIP_DOMAIN, and PUBLIC_IP
   ```
3. **Assemble Certificates**:
   Place the combined certificate file into `./certs/wss/wss.pem`:
   ```sh
   install -d -m 0750 ./certs/wss
   cat /path/to/fullchain.pem /path/to/privkey.pem > ./certs/wss/wss.pem
   chmod 0640 ./certs/wss/wss.pem
   ```
   *(Ensure UID 10001 has read access to `wss.pem` and execute permission on `./certs/wss`.)*
4. **Ingress and WSS Exposure Options**:
   By default, `compose.standalone.yaml` publishes `${WSS_PORT:-7443}:7443/tcp` on all host interfaces (`0.0.0.0`). Three common ingress patterns exist:
   - **Direct Public WSS on port 443** (`WSS_PORT=443`): Set `WSS_PORT=443` in `.env` to map `0.0.0.0:443 -> container 7443`. FreeSWITCH terminates TLS directly. Flutter clients connect directly to `wss://sip.example.com`. No reverse proxy is required. This requires that no other web server (Nginx, Apache, Caddy) binds port 443 on the host.
   - **Direct Public WSS on custom port 7443** (Default `WSS_PORT=7443`): Maps `0.0.0.0:7443 -> container 7443`. FreeSWITCH terminates TLS directly. Flutter clients must explicitly include the port in their WebSocket URL: `wss://sip.example.com:7443`. Useful when port 443 is already occupied by standard web traffic and clients can connect to high ports.
   - **Reverse-Proxy TCP/TLS Passthrough on port 443**: An edge reverse proxy (such as Traefik, HAProxy, Caddy with layer4, or Nginx with `stream`) listens on public port 443, inspects the TLS SNI header for `sip.example.com`, and passes the raw TLS byte-stream directly through to FreeSWITCH port `7443` without terminating TLS. FreeSWITCH terminates TLS using `wss.pem`. If the reverse proxy runs on the same host and you want to restrict FreeSWITCH's published port to loopback only, prefix the port mapping with `127.0.0.1:` (e.g. `127.0.0.1:${WSS_PORT:-7443}:7443/tcp`).
5. **Start Services**:
   ```sh
   docker compose -f compose.standalone.yaml up -d
   ```
   To use default `docker compose up` commands, copy or symlink the file:
   ```sh
   ln -s compose.standalone.yaml compose.override.yaml
   docker compose up -d
   ```

### Approach 2: Coolify VPS Deployment (`compose.yaml`)
In Coolify VPS environments:
1. **Literal Mount Path**: Coolify validates bind mount sources before evaluating Compose environment variables. Therefore, `compose.yaml` mounts the literal path `/opt/relay/certs/wss` into `/etc/freeswitch/wss`.
2. **Traefik TCP Passthrough**: Coolify's Traefik reverse proxy receives incoming traffic on port `443`. Traefik routes TCP traffic matching `HostSNI(${SIP_DOMAIN})` straight to FreeSWITCH on container port `7443` via `tls.passthrough=true`. FreeSWITCH terminates TLS directly.
3. **Deploying in Coolify**:
   - In Coolify UI, add a new **Docker Compose** resource from your repository.
   - Set the Compose location to `/compose.yaml`.
   - Add all environment variables from `.env.example` in Coolify's environment settings.
   - Assemble `wss.pem` at `/opt/relay/certs/wss/wss.pem` on the VPS as documented below.

### Firewall & Port Reference

| Port Range | Protocol | Handler | Purpose |
| :--- | :--- | :--- | :--- |
| `443` (or `7443`) | TCP | Traefik / FreeSWITCH | SIP over WSS signaling (TLS passthrough to FreeSWITCH `7443`) |
| `16384–16483` | UDP | FreeSWITCH | Direct DTLS-SRTP audio and video media streams |
| `3478` | UDP & TCP | coturn | STUN binding discovery and TURN allocations |
| `49160–49259` | UDP | coturn | TURN media relay traffic |
| `5066` *(Internal)* | TCP | FreeSWITCH | Plain WebSocket listener (container network only, never published) |
| `8021` *(Internal)* | TCP | FreeSWITCH | Event Socket Library on loopback `127.0.0.1` (never published) |

> [!WARNING]
> Docker bridge port mappings can bypass local UFW firewall rules on Linux hosts. Ensure cloud provider firewall rules (security groups) explicitly restrict and open only the necessary ports.

---

## Certificates and Native WSS

FreeSWITCH requires a single combined PEM file containing the leaf certificate, intermediate CA certificates, and unencrypted private key, in that order:

```text
-----BEGIN CERTIFICATE-----
(Leaf / Server Certificate)
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
(Intermediate CA Certificate)
-----END CERTIFICATE-----
-----BEGIN PRIVATE KEY-----
(Unencrypted Private Key)
-----END PRIVATE KEY-----
```

### Assembling `wss.pem` from Let's Encrypt / Certbot:
```sh
# For Coolify deployment:
sudo install -d -o 10001 -g 10001 -m 0750 /opt/relay/certs/wss
sudo sh -c '
  set -eu
  umask 077
  cat /etc/letsencrypt/live/sip.example.com/fullchain.pem \
      /etc/letsencrypt/live/sip.example.com/privkey.pem > /opt/relay/certs/wss/wss.pem.new
  chown 10001:10001 /opt/relay/certs/wss/wss.pem.new
  chmod 0640 /opt/relay/certs/wss/wss.pem.new
  mv /opt/relay/certs/wss/wss.pem.new /opt/relay/certs/wss/wss.pem
'
```

### Certificate Security, Permissions and Trust Model:
- **Read-Only Mount Protection vs. Read Access**: Mounting `/etc/freeswitch/wss` with `read_only: true` prevents the container process from modifying, tampering with, or deleting host certificate files. However, **read-only mounting does not prevent reading**. Any process running inside the container (UID 10001) can read the unencrypted private key material.
- **Ownership and File Modes**: On the host, the certificate directory should have permission `0750` with ownership `root:10001` (or `10001:10001`). The combined `wss.pem` file must have permission `0640` or `0600` owned by UID `10001`. This allows the non-root `freeswitch` container process to read the key while preventing unprivileged users on the host from reading private keys.
- **Trust Implications**: Because FreeSWITCH terminates TLS natively, it requires access to the unencrypted private key. Use a dedicated subdomain certificate (e.g., `sip.example.com`) rather than a root-domain wildcard certificate to restrict the security perimeter in case of host or container compromise.

### Reloading Certificates:
After replacing `wss.pem` during certificate renewal, reload the internal SIP profile without restarting the container:
```sh
docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "sofia profile internal restart"'
```
*(Note: Reloading the profile interrupts active calls.)*

---

## SIP Account Provisioning

Accounts are generated dynamically at container startup by [`scripts/configure.py`](file:///Users/quing/projects/relay_server/scripts/configure.py).

### How It Works:
- Individual passwords are derived deterministically:
  $$\text{Password} = \operatorname{HMAC-SHA256}(\text{SIP\_PASSWORD\_SEED}, \text{extension} + \text{"@"} + \text{SIP\_DOMAIN})[0:32]$$
- FreeSWITCH stores the SIP Digest A1 MD5 hash in `/etc/freeswitch/directory.xml`:
  $$\text{A1} = \operatorname{MD5}(\text{extension} + \text{":"} + \text{SIP\_DOMAIN} + \text{":"} + \text{Password})$$
- Plaintext passwords are **never written to disk**.
- Provisioning is **idempotent**: keeping `SIP_PASSWORD_SEED` and `SIP_DOMAIN` constant ensures passwords and authentication hashes remain identical across container restarts.

### Retrieving Credentials for a Test Account:
Run the credential lookup utility inside the FreeSWITCH container:
```sh
docker compose exec freeswitch python3 /opt/relay/configure.py credentials 1001
```

**Example Output:**
```text
URI: sip:1001@sip.example.com
Authorization username: 1001
Password: 9a7b4c... (32-character hex string)
```

---

## Configuring the Relay Flutter Client

Configure the Relay Flutter SIP stack with the provisioned credentials:

```text
WebSocket URL:          wss://sip.example.com
SIP URI:                sip:1001@sip.example.com
Authorization Username: 1001
Password:               <derived password from configure.py>
Target Destination:     sip:1002@sip.example.com
```

### WebRTC ICE Configuration:
```json
[
  {
    "urls": ["stun:turn.example.com:3478"]
  },
  {
    "urls": [
      "turn:turn.example.com:3478?transport=udp",
      "turn:turn.example.com:3478?transport=tcp"
    ],
    "username": "<TURN_USERNAME>",
    "credential": "<TURN_PASSWORD>"
  }
]
```

---

## Verification & Health Checks

### 1. Automated Regression Tests
Run the Python test suite to verify configuration rendering, range compaction, error sanitization, and Compose declarations:
```sh
python3 -m unittest discover -s tests -v
```

### 2. Service Health
Check container health probes:
```sh
docker compose ps
docker compose exec freeswitch python3 /opt/relay/health.py freeswitch
docker compose exec coturn python3 /opt/relay/health.py coturn
```

### 3. WSS Handshake and Certificate Verification
From an external computer with internet connectivity, verify TLS termination and WebSocket protocol upgrade:
```sh
# Verify TLS certificate chain and SNI
openssl s_client -connect sip.example.com:443 -servername sip.example.com -verify_hostname sip.example.com </dev/null

# Verify SIP WebSocket upgrade (HTTP 101 Switching Protocols)
curl --http1.1 --include --no-buffer --max-time 5 https://sip.example.com/ \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  -H 'Sec-WebSocket-Protocol: sip'
```

### 4. Self-Contained TURN Allocation Test
Verify STUN/TURN binding and allocation from within the coturn container:
```sh
docker compose exec coturn sh -c 'turnutils_uclient -y -u "$TURN_USERNAME" -w "$TURN_PASSWORD" -p "$TURN_PORT" "$PUBLIC_IP"'
```

> [!NOTE]
> Passing local health checks and unit tests verifies local syntax, container startup, and configuration rendering. It does not establish public internet reachability, TLS trust from external mobile devices, or successful UDP NAT traversal. Real two-device validation across separate cellular and Wi-Fi networks is required for full validation.

---

## Common Deployment Failures & Troubleshooting

1. **Mount Source Path Does Not Exist**:
   - *Symptom*: Docker Compose fails at startup with `bind source path does not exist`.
   - *Cause*: `create_host_path: false` prevents Docker from silently creating an empty directory when the host certificate directory is missing.
   - *Fix*: Create `./certs/wss` (standalone) or `/opt/relay/certs/wss` (Coolify) and place `wss.pem` inside before starting containers.
2. **Missing or Unreadable Certificate**:
   - *Symptom*: Container exits with code 1 and error: `WSS certificate missing or unreadable: mount a combined wss.pem in /etc/freeswitch/wss`.
   - *Cause*: `wss.pem` is absent, has 0 bytes, or is owned by root without read permissions for UID 10001.
   - *Fix*: Ensure `wss.pem` contains the private key and certificates, and run `chmod 0640 wss.pem && chown 10001:10001 wss.pem`.
3. **SIP Registration Works but No Audio or Video**:
   - *Symptom*: Call connects and timers advance, but no audio or video flows in either direction.
   - *Cause*: UDP media ports (`16384–16483`) are blocked by the VPS provider's security group, or `PUBLIC_IP` does not match the actual public IPv4 address.
   - *Fix*: Verify security group rules permit inbound UDP in the media range and verify `PUBLIC_IP` via `curl -4 ifconfig.me`.
4. **Calls Fail Over Cellular Networks (Symmetric NAT)**:
   - *Symptom*: Calls work on local Wi-Fi but fail when one device switches to cellular/mobile data.
   - *Cause*: Direct ICE candidate checks fail through symmetric carrier NAT, and coturn relay ports (`49160–49259`) are blocked or coturn credentials in the Flutter client are incorrect.
   - *Fix*: Confirm `TURN_USERNAME` and `TURN_PASSWORD` match the server environment, and verify coturn UDP relay ports are forwarded.
5. **Port Overlap Error at Startup**:
   - *Symptom*: Container startup fails with `ValueError: TURN listener, TURN relay range, and RTP range must not overlap`.
   - *Cause*: Ports defined in `RTP_START`–`RTP_END` and `TURN_MIN_PORT`–`TURN_MAX_PORT` overlap.
   - *Fix*: Adjust port ranges in `.env` so that RTP media (`16384–16483`) and TURN relays (`49160–49259`) remain completely disjoint.

---

## Safe Shutdown & Persistence Behavior

- **State Persistence**: The named Docker volume `freeswitch-state` is mounted to `/var/lib/freeswitch`. It retains SQLite registration databases (`/var/lib/freeswitch/db`) and generated media DTLS certificate keys (`/var/lib/freeswitch/tls`) across container restarts and updates.
- **Dynamic Configuration**: `/etc/freeswitch/` and `/etc/coturn/` configurations are re-rendered cleanly on container start without mutating volume state.
- **Graceful Termination**: `compose.yaml` and `compose.standalone.yaml` declare `stop_grace_period: 30s` for FreeSWITCH and `stop_grace_period: 10s` for coturn. Running `docker compose down` sends `SIGTERM`, allowing active SIP dialogs and media allocations to terminate cleanly before `SIGKILL` is sent.

---

## Logging, Security & Operational Hygiene

- **Seed Protection**: Treat `SIP_PASSWORD_SEED` as a master private key. If this seed is exposed, all derived SIP passwords can be calculated. Never commit `.env` or paste it in public forums.
- **Safe SIP Tracing**: Sofia SIP tracing captures plaintext SIP packets, which include digest nonces, callee numbers, and network topologies. Enable it only temporarily when diagnosing registration failures:
  ```sh
  docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "sofia profile internal siptrace on"'
  docker compose logs --since=2m freeswitch
  docker compose exec freeswitch sh -c 'fs_cli -p "$ESL_PASSWORD" -x "sofia profile internal siptrace off"'
  ```
- **Process List Protection**: Avoid running `fs_cli` with inline passwords on shared host multi-user environments. `scripts/health.py` sanitizes all output and exceptions to prevent accidental credential leakage in container logs.
- **Single Concurrent Registration**: `multiple-registrations=false` is enforced in `internal.xml`. Registering an extension from a second device replaces the active contact binding.

---

## Known Limitations

1. **No External PSTN or Transcoding**: Only Opus audio and VP8 video are supported. Incoming calls requesting unsupported codecs are rejected.
2. **Synchronous Calling Only**: There is no offline push notification service (Apple APNs or Google FCM), PBX voicemail, or call parking. A callee must maintain an active WebSocket connection and valid SIP registration to receive calls.
3. **Shared TURN Credentials**: All clients share a single set of long-term credentials (`TURN_USERNAME` / `TURN_PASSWORD`). Rate limiting is enforced via `max-bps=1000000` and `user-quota=20` in coturn.
4. **IPv4 WebRTC Candidates**: The ICE candidate ACL `relay-ice-v4` accepts public IPv4 candidates and RFC 1918 private candidates, filtering loopback, link-local, and CGNAT subnets. IPv6 candidates are ignored.

---

## Licensing & Third-Party Attribution

Relay Server's custom configuration templates, entrypoint scripts, configuration generators, and test suites are licensed under the **MIT License**. See [LICENSE](LICENSE) for the full text.

### Upstream Software Licenses:
The container images built by this repository compile and link upstream open-source software under their respective licenses:

| Software | License | Upstream Project | License Location in Image |
| :--- | :--- | :--- | :--- |
| **FreeSWITCH** | MPL 1.1 | [SignalWire / FreeSWITCH](https://github.com/signalwire/freeswitch) | `/usr/local/share/licenses/relay-freeswitch/LICENSE` |
| **Sofia-SIP** | LGPL 2.1 | [FreeSWITCH / Sofia-SIP](https://github.com/freeswitch/sofia-sip) | `/usr/local/share/licenses/sofia-sip/COPYING` |
| **SpanDSP** | LGPL 2.1 | [FreeSWITCH / SpanDSP](https://github.com/freeswitch/spandsp) | `/usr/local/share/licenses/spandsp/COPYING` |
| **coturn** | BSD 3-Clause | [coturn / coturn](https://github.com/coturn/coturn) | `/usr/local/share/licenses/coturn/LICENSE` |

Source repository commit hashes are pinned in `freeswitch/Dockerfile` and `coturn/Dockerfile` to ensure build reproducibility. Upstream licenses are preserved and copied directly into `/usr/local/share/licenses/` during the build stages.

> [!NOTE]
> This repository distributes original source code and configuration templates under the MIT License. It does not distribute pre-compiled binary packages or pre-built container images. Anyone building, packaging, or distributing container images or binaries derived from upstream components is responsible for complying with the respective upstream licensing obligations (including MPL 1.1, LGPL 2.1, and BSD 3-Clause requirements).
