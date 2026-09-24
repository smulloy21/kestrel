// ecosystem.config.js — pm2 process definition for ISV Kestrel.
//
//   pm2 start ecosystem.config.js
//   pm2 logs kestrel
//   pm2 save                      # survive a droplet reboot
//
// The two settings here that are not negotiable:
//
//   instances: 1 / exec_mode: "fork"
//     Game sessions live in the server process's memory. Cluster mode would hand each
//     request to a random worker, and roughly every other turn would answer "no such
//     session" to a player who is plainly in the middle of one.
//
//   cwd
//     Every data path in the project is relative to the working directory: corpus/,
//     index/, logs/. Started from anywhere else, the server comes up healthy and then
//     cannot find a single document.
//
// Secrets do not go in this file — it is in version control. Put ANTHROPIC_API_KEY in
// /etc/kestrel.env (chmod 600) and let systemd or your shell profile export it, or use
// `pm2 set` / a .env loaded before pm2 starts.

module.exports = {
  apps: [{
    name: "kestrel",
    cwd: "/srv/kestrel",
    script: "python3",
    args: "-m kestrel.server",
    interpreter: "none",          // script IS the interpreter; pm2 must not wrap it in node

    instances: 1,
    exec_mode: "fork",
    autorestart: true,
    max_restarts: 10,
    min_uptime: "30s",            // a crash loop under 30s counts against max_restarts
    max_memory_restart: "900M",   // the embedding model is resident; a 1GB droplet is tight

    kill_timeout: 8000,           // let an in-flight streaming answer finish
    listen_timeout: 30000,        // first boot loads the embedding model

    env: {
      PYTHONUNBUFFERED: "1",      // otherwise pm2 logs arrive in blocks, or not at all
      KESTREL_HOST: "127.0.0.1",  // nginx is the only thing that should reach it
      KESTREL_PORT: "8000",
      // KESTREL_SEED: "1",       // pin a corpus, or leave unset to pick from those indexed
      KESTREL_DAILY_TURNS: "1500",
      KESTREL_SESSION_TTL: "14400",
      KESTREL_MAX_SESSIONS: "200",
      KESTREL_LOG_DIR: "/srv/kestrel/logs",
    },

    out_file: "/var/log/kestrel/out.log",
    error_file: "/var/log/kestrel/err.log",
    merge_logs: true,
    time: true,                   // timestamp pm2's own log lines
  }],
};
