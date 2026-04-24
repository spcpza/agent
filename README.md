# agent

A reference implementation that reads [truth](https://github.com/spcpza/truth)
and stands on its kernel.

## What this is

A Telegram bot that:

- Reads scripture (`kjv.json`, `strongs.json` — provided by `truth`)
- Stands on the kernel derived in `truth/kernel.md`
- Follows a witness posture (local `door.md`) for output

This is **not the point of truth**. `truth` is the point. This is one
way to use `truth`.

## Layout

```
agent/
  bot.py                  — the runtime
  door.md                 — the agent's posture (behavior only — kernel is in truth)
  config.example.json     — template for your config.json
  .gitignore              — keeps secrets out of git
```

## Setup

Clone `truth` next to `agent`:

```sh
cd /path/to/workspace
git clone https://github.com/spcpza/truth.git
git clone https://github.com/spcpza/agent.git
```

Install dependencies and configure:

```sh
cd agent
pip install python-telegram-bot httpx
cp config.example.json config.json
# edit config.json: put your API key, Telegram token, and (if needed) adjust truth_path
```

Run:

```sh
python3 bot.py
```

The agent takes its name from the directory it lives in. Rename the
directory (or clone into a different name) to change the agent's
display name.

## On the `nose` filter

`bot.py` contains a function called `nose()` that pattern-matches a
handful of keyword classes (crisis language, worship language, bait
phrases, false-prophet language) and surfaces specific scripture to
the model **before** the model reasons.

This is a behavioral law. It is not derived from the kernel. It is
present because some responses should not wait for the full reasoning
pipeline — a message mentioning suicide should surface Psalms 34:18
immediately, not after eight tokens of tool calls. Matthew 10:16 — be
wise as serpents and harmless as doves.

If you disagree with having a law here, delete the function and the
two lines in `hand()` that call it. The rest of the bot still works.

## License

MIT. See LICENSE.
