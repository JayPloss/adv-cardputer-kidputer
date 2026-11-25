#!/bin/sh
# Installs tools/gate/post-commit as this clone's post-commit hook.
root=$(git rev-parse --show-toplevel) || exit 1
ln -sf ../../tools/gate/post-commit "$root/.git/hooks/post-commit"
chmod +x "$root/tools/gate/post-commit" "$root/tools/gate/run-gate.py"
echo "post-commit hook installed"
