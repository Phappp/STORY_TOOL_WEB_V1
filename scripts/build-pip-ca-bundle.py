"""Build a per-venv pip bundle from its certifi roots and a verified DER root."""

import pathlib
import ssl
import sys

from pip._vendor import certifi


if len(sys.argv) != 3:
    raise SystemExit("usage: build-pip-ca-bundle.py ROOT.der OUTPUT.pem")

root_path = pathlib.Path(sys.argv[1])
bundle_path = pathlib.Path(sys.argv[2])
base_bundle = pathlib.Path(certifi.where()).read_text(encoding="ascii")
root_pem = ssl.DER_cert_to_PEM_cert(root_path.read_bytes())
bundle_path.write_text(base_bundle.rstrip() + "\n" + root_pem, encoding="ascii")
print("Configured a venv-only CA bundle from certifi and the verified Windows-trusted root.")
