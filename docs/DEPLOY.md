# Deploying to a droplet with nginx and pm2

Written for a box that already runs nginx with a domain and TLS. Replacing whatever is
there now with this.

## The two things that will bite you

**nginx buffers proxied responses by default.** KES's answers stream, a token at a time,
over a newline-delimited JSON body. With buffering on, nginx holds the whole response and
delivers it in one lump — so the game appears to hang for several seconds and then dump a
paragraph. Everything "works", and the single best thing about the interface is gone. The
fix is three lines in the location block below.

**Sessions live in the server process's memory.** One instance, fork mode, never cluster.
Two workers means every other request lands on a process that has never heard of your
session, and the player gets "No such session" mid-run. This is why `ecosystem.config.js`
pins `instances: 1`.

## 1. Get the code and the data onto the box

```bash
ssh you@droplet
sudo mkdir -p /srv/kestrel /var/log/kestrel
sudo chown -R $USER:$USER /srv/kestrel /var/log/kestrel
```

From your laptop — the code, then the data:

```bash
rsync -av --exclude .git --exclude __pycache__ --exclude .venv \
      --exclude corpus --exclude index --exclude logs \
      ./ you@droplet:/srv/kestrel/

# Ship the corpus and the prebuilt index rather than regenerating on the droplet:
# generating costs API tokens, and building the index downloads an embedding model onto
# a box that may not have the RAM to enjoy it.
rsync -av corpus/ you@droplet:/srv/kestrel/corpus/
rsync -av index/  you@droplet:/srv/kestrel/index/
```

The index is a Chroma directory (SQLite plus vectors). It moves fine as long as the
droplet installs the same `chromadb` version — which `requirements.txt` pins closely
enough. If it complains on startup, rebuild in place with
`python3 -m kestrel.index --seed 1`.

## 2. Python environment

```bash
cd /srv/kestrel
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then check it can actually see the archive, before nginx and pm2 are involved:

```bash
.venv/bin/python -m kestrel.ship                 # simulation self-tests, no API calls
.venv/bin/python -m evals.retrieval --seed 1     # proves the index loaded
```

If those pass, the hard part is done.

## 3. The API key

Not in the repo, not in `ecosystem.config.js`, which is in version control.

```bash
sudo install -m 600 /dev/null /etc/kestrel.env
sudo tee /etc/kestrel.env >/dev/null <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
EOF
```

Load it into the environment pm2 will inherit. The simplest reliable way:

```bash
set -a && . /etc/kestrel.env && set +a && pm2 start ecosystem.config.js --update-env
```

pm2 captures the environment at start, so after changing the key you must
`pm2 restart kestrel --update-env`, not just `pm2 restart kestrel`.

## 4. pm2

`ecosystem.config.js` points `script` at `python3`. Change it to the venv interpreter:

```js
script: "/srv/kestrel/.venv/bin/python",
```

Then:

```bash
cd /srv/kestrel
pm2 delete old-game              # whatever is there now
set -a && . /etc/kestrel.env && set +a
pm2 start ecosystem.config.js --update-env
pm2 logs kestrel --lines 40      # expect "index v1 loaded"
pm2 save                         # survive a reboot
```

`pm2 startup` once, if you have not already, so pm2 itself comes back after a reboot.

## 5. nginx

Replace the old game's location block:

```nginx
location / {
    proxy_pass         http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Real-IP         $remote_addr;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;

    # THE IMPORTANT PART. Without these three, KES's answers arrive in one lump
    # after a long pause instead of streaming, and the game feels broken.
    proxy_buffering    off;
    proxy_cache        off;
    chunked_transfer_encoding on;

    # A turn is an LLM call. The default 60s read timeout will cut off a slow one.
    proxy_read_timeout 180s;
    proxy_send_timeout 180s;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## 6. Check it

```bash
curl -s localhost:8000/healthz          # {"ok":true,"corpora":["v1"],...}
curl -s https://yourdomain/healthz      # same, through nginx
```

Then open the site and ask KES something. **Watch whether the answer streams.** If it
appears all at once after a pause, `proxy_buffering off` did not take effect — check you
edited the right server block.

## Running costs

A public URL is a public API key. Two limits are on by default:

| Setting | Default | What it does |
| --- | --- | --- |
| `KESTREL_DAILY_TURNS` | 1500 | questions per day across everyone; 0 disables |
| `KESTREL_SESSION_TTL` | 14400 | seconds before an idle run is dropped from memory |
| `KESTREL_MAX_SESSIONS` | 200 | hard ceiling on concurrent runs |

At roughly four cents a turn, 1500 turns is about $60 on the worst possible day — which is
a ceiling, not an expectation, but set it to something you would not mind losing. Watch
`/healthz` for `turns_today` in the first week.

If the game is only meant to be seen by people you send it to, the cheapest real protection
is nginx basic auth on `/`, or an allowlist. A budget cap is a backstop, not a lock.

## Afterwards

Session logs land in `/srv/kestrel/logs/`, one JSONL file per playthrough. Pull them down
and read them with the tools you already have:

```bash
rsync -av you@droplet:/srv/kestrel/logs/ ./logs/
python -m kestrel.report
python -m kestrel.report --ungrounded
```

Those are real players, not you — which makes them the best material you will get for
tuning the corpus and for adding golden questions in the phrasings people actually use.

`logs/` will grow slowly (a few KB per session). Worth a `find logs -mtime +60 -delete` in
cron eventually, not urgently.
