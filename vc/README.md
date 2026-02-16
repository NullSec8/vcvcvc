# Terminal Voice Chat (Python)

Two-user terminal-based real-time voice chat over the internet (client-server relay).

## Quick Start (Beginner Friendly)

1) Start the server in terminal #1 (no flags needed):

```powershell
python -m voicechat.server
```

2) Start client A in terminal #2:

```powershell
python -m voicechat.client
```

3) Start client B in terminal #3:

```powershell
python -m voicechat.client
```

Both server and client now include a setup wizard and prompt for all missing values.

## Features

- Real-time UDP voice relay
- WebSocket signaling and optional text fallback (`/msg`)
- Automatic reconnect with backoff
- Simple auth (`username`, `room`, optional shared token)
- Colorized terminal UI (status tags, warnings, chat highlights)
- Guided setup wizard for both server and client (no args required)
- Python 3.10+ on Windows/Linux terminal

## Install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## Run Server

```powershell
python -m voicechat.server --host 0.0.0.0 --ws-port 8765 --udp-port 9999
```

## Local Server (same PC / same LAN)

Use loopback for local-only testing:

```powershell
python -m voicechat.server --host 127.0.0.1 --ws-port 8765 --udp-port 9999
```

Clients on the same machine:

```powershell
python -m voicechat.client --server 127.0.0.1 --ws-port 8765 --udp-port 9999 --username alice --room room1
python -m voicechat.client --server 127.0.0.1 --ws-port 8765 --udp-port 9999 --username bob --room room1
```

Clients on same LAN (different devices):

- Start server with `--host 0.0.0.0`
- Use your LAN IP for `--server` (example `192.168.1.10`)

Optional shared token (server-side):

```powershell
$env:VOICECHAT_ROOM_TOKEN="my-secret"
python -m voicechat.server --host 0.0.0.0 --ws-port 8765 --udp-port 9999
```

## Run Client

```powershell
python -m voicechat.client --server <SERVER_IP> --ws-port 8765 --udp-port 9999 --username alice --room room1 --token my-secret
```

Second user:

```powershell
python -m voicechat.client --server <SERVER_IP> --ws-port 8765 --udp-port 9999 --username bob --room room1 --token my-secret
```

## Online Server (VPS)

1) Upload project to VPS and install dependencies:

```bash
sudo apt update
sudo apt install -y python3 python3-venv
mkdir -p ~/voicechat
cd ~/voicechat
# copy project files here
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

2) Open firewall ports on VPS:

```bash
sudo ufw allow 8765/tcp
sudo ufw allow 9999/udp
sudo ufw enable
sudo ufw status
```

3) Start server on all interfaces:

```bash
export VOICECHAT_ROOM_TOKEN="my-secret"
source .venv/bin/activate
python -m voicechat.server --mode public --ws-port 8765 --udp-port 9999
```

Equivalent explicit host form:

```bash
python -m voicechat.server --host 0.0.0.0 --ws-port 8765 --udp-port 9999
```

4) Connect from anywhere using VPS public IP:

```powershell
python -m voicechat.client --server <VPS_PUBLIC_IP> --ws-port 8765 --udp-port 9999 --username alice --room global-room --token my-secret
```

```powershell
python -m voicechat.client --server <VPS_PUBLIC_IP> --ws-port 8765 --udp-port 9999 --username bob --room global-room --token my-secret
```

### Optional: Run server as a Linux service (systemd)

Use [deploy/voicechat.service](deploy/voicechat.service), then:

```bash
sudo cp deploy/voicechat.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable voicechat
sudo systemctl start voicechat
sudo systemctl status voicechat
```

Set your token in the service file before enabling it.

## Terminal Commands (client)

- `/msg hello` send fallback text message to peer
- `/status` show current connection/session info
- `/help` show command list
- `/quit` disconnect

## Notes

- Open firewall ports for both WebSocket TCP and UDP relay.
- This is MVP quality: no codec compression/encryption yet.
- For internet use, prefer a VPS static IP and secure your room with `VOICECHAT_ROOM_TOKEN`.