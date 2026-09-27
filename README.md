# Overmod

Overmod is a small, self-hosted catalogue for Overcooked! 2 mods. It stores only
names, descriptions, authors and external download instructions; it never hosts
mod files. The public site also reads cached seven-day custom-level popularity
data from an Overrank server.

## Local test

Use the existing `release` Conda environment:

```powershell
conda run -n release python run_server.py
```

Then open <http://127.0.0.1:3007>. Administration is kept on the separate
<http://127.0.0.1:3007/admin> page. The local-only administrator token in the
ignored `.env` file is `overmod-local-admin`. Replace it before any deployment.

Run tests with:

```powershell
conda run -n release python -m unittest discover -s tests -p "test_*.py" -v
```

Import known custom levels from a sibling Overrank database with:

```powershell
python import_overrank_levels.py
```

An explicit database path can be supplied as the first argument. Existing
catalogue entries are left unchanged.

## Configuration

Copy `.env.example` to `.env`. Generate a production administrator token and its
hash with:

```powershell
python server_generate_admin_token.py
```

Keep the printed token private and place only the generated SHA-256 line in
`.env`. `OVERMOD_OVERRANK_API_KEY` must match the Overrank server key. During
local development Overmod can also read that key from a sibling `Overrank/.env`.

## HTTP deployment

Overmod supports direct administration over HTTP: open `/admin` and enter the
administrator token. This is deliberately simple, but plain HTTP does not encrypt
the token in transit. Use a long random token, do not reuse a password, and replace
it immediately if it may have been exposed.
