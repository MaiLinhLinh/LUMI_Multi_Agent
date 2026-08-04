# Gemini Live multi-domain application

This directory is intentionally independent from `code_toolcall`.

The target architecture is:

```text
LiveSessionOrchestrator
  -> LiveDomainRegistry
       -> WeatherLiveDomain
       -> MusicLiveDomain (future)
  -> Presentation Compiler / frontend events
```

Each domain owns its tool declarations, compact context resolution, factual
adapter, template capabilities, and domain prompt guidance.  The shared Live
core only owns the Gemini Live session, tool dispatch, bounded session memory,
and safe delivery of approved presentation scenes.

## Migration checkpoints

1. Create the independent domain interface and registry. (current)
2. Copy the minimal shared Live core and configuration into this directory.
3. Move Weather into `domains/weather/` with no imports from `code_toolcall`.
4. Move the standalone web frontend and verify Weather end-to-end.
5. Add a second domain only through registry registration to verify extension.

`code_toolcall` remains unchanged during this migration and remains the
reference implementation until the new application has passed verification.

## Run the independent web app

Copy `.env.example` to `.env` inside this folder and set `GEMINI_API_KEY`,
`GEMINI_LIVE_API_KEY`, and the Redis settings. This application intentionally
does not read `code_toolcall/.env`.

```powershell
conda activate LumiMultiAgent
python -m gemini_live.web_app
```

Open `http://127.0.0.1:8002`.
