---
name: goblin:install
description: Install the goblin CLI locally using pipx
disable-model-invocation: true
---

Run the install script to build and install the goblin CLI:

```bash
bash ./install.sh
```

If the script fails, read the error output and help the user resolve it. Common issues:
- **Python not found**: They need Python 3.11+ installed
- **Python version too old**: They need to upgrade to 3.11+
- **pipx not found**: Direct them to https://pipx.pypa.io/stable/installation/
