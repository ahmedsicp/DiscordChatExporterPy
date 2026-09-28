# DiscordChatExporterPy

A single-file Python script that exports Discord server/private-channel chat history into styled, self-contained HTML chatlogs.

Written from scratch as a single-script reimplementation of the classic **DiscordChatExporter** workflow, without external exporter dependencies.

## Features

- Exports message history from any channel the authenticated bot/user can read
- Renders self-contained HTML chatlogs with Discord-style styling (logos, avatars, attachment/file icons, pinned-message and thread indicators)
- Library-agnostic: dynamically supports `nextcord`, `disnake`, or `discord.py` — whichever is installed
- Handles emoji (including grapheme-aware rendering), timezone-aware timestamps, and message references/interactions
- Attachment and embed rendering via CDN asset URLs
- CLI-driven with `argparse` options

## Requirements

- Python 3.7+
- `aiohttp`
- `emoji`, `pytz`, `grapheme`
- One of: `discord.py`, `nextcord`, or `disnake`

## Usage

```bash
python DiscordChatExporter.py --help
```

Set your bot/user token via the standard Discord library configuration, then run the script against the channel(s) you want to export.

## License

See the upstream DiscordChatExporter project for attribution conventions; this is a single-script working version maintained for personal archival use.
