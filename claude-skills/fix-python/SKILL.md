---
name: fix-python
description: "CloudByte: install a signed Python so Windows Smart App Control does not block the plugin"
disable-model-invocation: true
---
The user asked CloudByte to install a signed Python. Run this exact command with your shell tool, with a 10 minute timeout, and do nothing else:

uv run -q --no-sync --directory "${CLAUDE_PLUGIN_ROOT}" -m src.common.windows_signed_python install

It downloads Python 3.12 from python.org, checks that the Python Software Foundation signed it, installs it for this user only, and moves the CloudByte plugin onto it. When a signed Python is already installed, it only moves the plugin onto that one.

Then show the user everything it printed, word for word. If it fails, show its error output and do not try anything else.
