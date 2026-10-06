# Local runtime exclusions for Genealogy-App, whose .gitignore is protected by PSTP.
# This helper is sourced after changing to (or identifying) the clone root.
exclude=$(git -C "$root" rev-parse --git-path info/exclude)
[[ $exclude = /* ]] || exclude="$root/$exclude"
mkdir -p "$(dirname "$exclude")"
for pattern in '.venv/' 'node_modules/' 'dist/' '__pycache__/' '*.pyc'; do
 if ! grep -qxF "$pattern" "$exclude" 2>/dev/null; then printf '%s\n' "$pattern" >> "$exclude"; fi
done
if ! git -C "$root" ls-files --error-unmatch package-lock.json >/dev/null 2>&1; then
 if ! grep -qxF 'package-lock.json' "$exclude" 2>/dev/null; then printf '%s\n' 'package-lock.json' >> "$exclude"; fi
fi
