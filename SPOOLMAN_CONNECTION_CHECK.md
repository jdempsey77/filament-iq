# Spoolman Connection Diagnosis

## Issue

`curl http://192.168.4.124:8080/api/v1/info` from HA host fails:
```
curl: (7) Failed to connect to 192.168.4.124 port 8080 after 7 ms: Couldn't connect to server
```

Since Spoolman runs on the HA host, this means either:
1. Spoolman is not running
2. Spoolman is listening only on `localhost` (not on the network interface `192.168.4.124`)
3. Spoolman is running on a different port
4. Firewall is blocking the connection

---

## Diagnostic Commands (run on HA host)

### 1. Check if Spoolman is running

**If Spoolman is a Docker container:**
```bash
docker ps | grep spoolman
# Should show a running container with port 8080 exposed
```

**If Spoolman is a systemd service:**
```bash
systemctl status spoolman
# Or:
ps aux | grep spoolman
```

**If Spoolman is a Python process:**
```bash
ps aux | grep spoolman
# Or check listening ports:
netstat -tlnp | grep 8080
# Or:
ss -tlnp | grep 8080
```

### 2. Try localhost (if Spoolman is running)

```bash
curl http://localhost:8080/api/v1/info
# Or:
curl http://127.0.0.1:8080/api/v1/info
```

**If localhost works but 192.168.4.124:8080 doesn't:**
- Spoolman is listening only on `127.0.0.1` (localhost), not on `0.0.0.0` (all interfaces)
- Need to configure Spoolman to bind to `0.0.0.0` or the specific interface

### 3. Check what Spoolman is listening on

```bash
# See what's listening on port 8080:
netstat -tlnp | grep 8080
# Or:
ss -tlnp | grep 8080

# Output will show something like:
# tcp  0  0  127.0.0.1:8080  0.0.0.0:*  LISTEN  12345/python
#            ^^^^^^^^^^^ this is the bind address
# 127.0.0.1 = localhost only
# 0.0.0.0 = all interfaces (what we need for HA integration to work)
```

---

## Fix Based on Findings

### A. Spoolman is NOT running

**Start Spoolman:**
- Docker: `docker start spoolman` (or `docker-compose up -d` if using compose)
- Systemd: `systemctl start spoolman`
- Manual: run your Spoolman start command

### B. Spoolman is listening on localhost only

**Need to configure Spoolman to listen on 0.0.0.0**

**If Spoolman is Docker:**

Check your `docker run` command or `docker-compose.yml`:
```yaml
# docker-compose.yml
services:
  spoolman:
    image: ghcr.io/donkie/spoolman:latest
    ports:
      - "8080:8080"  # This maps host 8080 to container 8080
      # If this is correct, the container should be reachable
    environment:
      - SPOOLMAN_HOST=0.0.0.0  # Make sure this is set (not 127.0.0.1)
```

Restart the container after changes.

**If Spoolman is a Python app:**

Check the Spoolman config or start command. It should bind to `0.0.0.0`:
```bash
# Example (adjust to your actual command):
spoolman --host 0.0.0.0 --port 8080
```

Or in Spoolman's config file (often `config.yaml` or `.env`):
```yaml
host: 0.0.0.0  # not 127.0.0.1 or localhost
port: 8080
```

### C. Spoolman is on a different port

If you find Spoolman listening on a port other than 8080 (e.g., 7912):
- Update the HA Spoolman integration URL to match: `http://localhost:7912`

---

## Home Assistant Integration URL

Once Spoolman is accessible, update the HA integration:

**Settings → Devices & Services → Spoolman → Configure**

**If Spoolman is listening on localhost only (127.0.0.1):**
- Set URL to: `http://localhost:8080` (or `http://127.0.0.1:8080`)
- This only works if HA and Spoolman are on the **same host** (not in separate containers with separate network namespaces)

**If Spoolman is listening on 0.0.0.0 (all interfaces):**
- Can use: `http://192.168.4.124:8080` (the host's IP)
- Or: `http://localhost:8080` (simpler if both are on the same host)

**Save** → HA will reload the integration and retry setup.

---

## Expected Result

After fixing Spoolman connectivity and updating the HA integration URL:

1. `curl http://localhost:8080/api/v1/info` from HA host returns JSON
2. HA Spoolman integration setup completes quickly (no "waiting" messages in logs)
3. Entities like `sensor.spoolman_spool_9` appear in **Developer Tools → States**
4. Template sensors become available and dropdown populates

---

## Next Steps

Run the diagnostic commands above and report:
1. Is Spoolman running? (docker ps / systemctl status / ps aux)
2. Does `curl http://localhost:8080/api/v1/info` work?
3. What does `netstat -tlnp | grep 8080` or `ss -tlnp | grep 8080` show?

Then we can fix the Spoolman configuration and HA integration URL accordingly.
