# Multiprofile Linux

Native Linux multi-profile Chromium manager (Python / Flask + profile extension).

This private repo is the source of truth for upcoming changes: app source, packaging, docs, and release `.deb` artifacts.

## Dev

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui]"
python -m multiprofile --server-only
# open http://127.0.0.1:8765/
```

## Build .deb

```bash
./build-deb.sh
# => dist/multiprofile-native_1.0.0_amd64.deb
```

## Install

```bash
sudo apt install ./dist/multiprofile-native_1.0.0_amd64.deb
multiprofile
```

**Version:** 1.0.0
