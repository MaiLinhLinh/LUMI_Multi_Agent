# Lumi Studio

Standalone local tool for drafting Lumi prompts and domains without changing
`gemini_live_2`. Run it with the same virtual environment as Lumi:

```powershell
conda run -n LumiMultiAgent python studio_app.py
```

Open `http://127.0.0.1:8003`.

## Test microphone from another device on the LAN

The sandbox always uses HTTPS at port `8005`, so Browser microphone and typed
input work from another device. Create a LAN certificate once on the Studio
machine, using its LAN IPv4 address:

```powershell
.\scripts\create_lan_tls.ps1 -LanIp 10.10.60.80
```

Import `certs/lumi-lan-ca-cert.pem` into **Trusted Root Certification
Authorities** on each test device. Never distribute either `.pem` private key.
Then start or update the sandbox and open `https://10.10.60.80:8005`.

Drafts and sandbox copies live only under `lumi_studio/workspace/`. The source
project remains read-only. A sandbox is a copied `gemini_live_2` project with
the current draft overlaid, started on a free port from 8004.

Studio follows the current Live prompt flow:

- Core Live guidance and shared Presentation context guidance load when Live opens.
- A domain presentation prompt is attached once, with the first `route_request`
  response for that domain in a Live connection.
- `SURFACE_READY` carries only the new panel identity, revision, Stage Map and
  available effects.

Saving a prompt always creates an override under `workspace/drafts/current`.
The source project is never overwritten; only a sandbox receives the overlay.
New draft domains also appear in the Prompt Catalog, where their presentation
and Plan Agent prompts can be edited before starting a sandbox.
