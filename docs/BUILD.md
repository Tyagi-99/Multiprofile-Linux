# Build
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ui]"
./scripts/test.sh
./build-deb.sh
