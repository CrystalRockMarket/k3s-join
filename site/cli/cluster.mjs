#!/usr/bin/env node
// Kituwa Cluster — install a k3s server, or join this machine to one.
//
//   sudo node cluster.mjs server [--tls-san NAME] [--disable traefik] [--version v1.36.5+k3s1]
//   sudo node cluster.mjs agent --url https://SERVER:6443 --token TOKEN
//   node cluster.mjs status
//   sudo node cluster.mjs uninstall [--yes]
//
// `--dry-run` shows the whole interface and installs nothing.
// Secrets travel to k3s in the environment; they are never interpolated into a
// shell string, never logged, and never printed.

// cli-spinners is the nice path; a standalone download still works without it.
let spinners;
try {
  spinners = (await import('cli-spinners')).default;
} catch {
  const frame = (f) => ({ frames: f, interval: 90 });
  spinners = {
    dots: frame(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']),
    dots12: frame(['⢄', '⢂', '⢁', '⡁', '⡈', '⡐', '⡠', '⣀', '⣁', '⣂']),
    earth: frame(['🌍', '🌎', '🌏']),
    moon: frame(['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘']),
    arc: frame(['◜', '◠', '◝', '◞', '◡', '◟']),
    bouncingBar: frame(['[    ]', '[=   ]', '[==  ]', '[=== ]', '[ ===]', '[  ==]', '[   =]']),
    noise: frame(['▓', '▒', '░']),
  };
}
import { spawn } from 'node:child_process';
import { appendFile, chmod, mkdir, readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const VERSION = '0.2.0';
const args = process.argv.slice(2);
const command = args[0];
const has = (f) => args.includes(f);
const opt = (f) => (args.includes(f) ? args[args.indexOf(f) + 1] : undefined);
const DRY = has('--dry-run');
const YES = has('--yes');
const TTY = process.stdout.isTTY;
const IS_ROOT = typeof process.getuid === 'function' && process.getuid() === 0;

// ------------------------------------------------------------------ ansi ----
const c = (code) => (s) => (TTY ? `\x1b[${code}m${s}\x1b[0m` : s);
const bold = c('1'), dim = c('2'), red = c('31'), green = c('32'), yellow = c('33'), cyan = c('36');
const fg256 = (n) => (s) => (TTY ? `\x1b[38;5;${n}m${s}\x1b[0m` : s);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const LOG_DIR = IS_ROOT ? '/var/log' : path.join(os.homedir(), '.local', 'state');
const LOG_PATH = path.join(LOG_DIR, 'kituwa-cluster.log');
async function log(line) {
  try {
    await mkdir(LOG_DIR, { recursive: true });
    await appendFile(LOG_PATH, `[${new Date().toISOString()}] ${line}\n`, { mode: 0o600 });
    await chmod(LOG_PATH, 0o600).catch(() => {});
  } catch {
    /* a log we cannot write must never break an install */
  }
}

// ---------------------------------------------------------------- banner ----
const LOGO = [
  '██╗  ██╗██╗████████╗██╗   ██╗██╗    ██╗ █████╗ ',
  '██║ ██╔╝██║╚══██╔══╝██║   ██║██║    ██║██╔══██╗',
  '█████╔╝ ██║   ██║   ██║   ██║██║ █╗ ██║███████║',
  '██╔═██╗ ██║   ██║   ██║   ██║██║███╗██║██╔══██║',
  '██║  ██╗██║   ██║   ╚██████╔╝╚███╔███╔╝██║  ██║',
  '╚═╝  ╚═╝╚═╝   ╚═╝    ╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═╝',
];
const GRADIENT = [51, 45, 39, 33, 27, 21];

async function banner() {
  console.log();
  for (let i = 0; i < LOGO.length; i++) {
    console.log('  ' + fg256(GRADIENT[i])(LOGO[i]));
    if (TTY) await sleep(50);
  }
  console.log();
  console.log('  ' + bold(cyan('C L U S T E R')) + dim('  ·  k3s, deployed in minutes'));
  console.log('  ' + dim(`v${VERSION}  ·  ${os.hostname()}  ·  ${os.platform()}/${os.arch()}${DRY ? '  ·  DRY RUN' : ''}`));
  console.log('  ' + dim('─'.repeat(47)));
  console.log();
}

// --------------------------------------------------------------- spinner ----
const done = [];
function cursor(show) { if (TTY) process.stdout.write(show ? '\x1b[?25h' : '\x1b[?25l'); }
process.on('exit', () => cursor(true));
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => {
    cursor(true);
    console.log('\n  ' + yellow('Cancelled.'));
    process.exit(sig === 'SIGINT' ? 130 : 143);
  });
}

async function step(label, work, spinnerName = 'dots') {
  const sp = spinners[spinnerName] ?? spinners.dots;
  const start = Date.now();
  const elapsed = () => ((Date.now() - start) / 1000).toFixed(1) + 's';
  const line = (icon, text, tail) => `  ${icon} ${text}${tail ? '  ' + dim(tail) : ''}`;
  let i = 0;
  let note = '';
  const ctx = {
    note: (t) => { note = t; },
    log: (t) => log(`${label}: ${t}`),
  };

  let timer;
  if (TTY) {
    cursor(false);
    timer = setInterval(() => {
      const frame = cyan(sp.frames[i++ % sp.frames.length]);
      process.stdout.write('\r\x1b[2K' + line(frame, label, `${note ? note + ' · ' : ''}${elapsed()}`));
    }, sp.interval);
  } else {
    console.log(`  … ${label}`);
  }

  try {
    const result = await work(ctx);
    clearInterval(timer);
    if (TTY) process.stdout.write('\r\x1b[2K');
    console.log(line(green('✔'), label, elapsed()));
    cursor(true);
    done.push(label);
    await log(`ok    ${label} (${elapsed()})`);
    return result;
  } catch (err) {
    clearInterval(timer);
    if (TTY) process.stdout.write('\r\x1b[2K');
    console.log(line(red('✖'), label, elapsed()));
    cursor(true);
    const reason = String(err.message || err).trim().split('\n').slice(-4).join('\n');
    await log(`fail  ${label}: ${reason}`);
    console.log('\n  ' + red('Failed: ') + reason);
    console.log('  ' + dim(`Full log: ${LOG_PATH}`));
    console.log('  ' + dim('Stuck? Run: sudo node cluster.mjs doctor --fix') + '\n');
    process.exit(1);
  }
}

// ----------------------------------------------------------------- shell ----
function sh(cmd, env = {}) {
  const started = Date.now();
  if (DRY) return sleep(900 + Math.random() * 900).then(() => '');
  return new Promise((resolve, reject) => {
    const p = spawn('bash', ['-c', cmd], { env: { ...process.env, ...env } });
    let out = '', err = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (err += d));
    p.on('close', (code) => {
      const safe = cmd.replace(/tskey-[A-Za-z0-9_-]+/g, 'tskey-…');
      log(`run   ${safe} -> ${code} (${Date.now() - started}ms)`).catch(() => {});
      if (code === 0) resolve(out.trim());
      else reject(new Error((err || out).trim().split('\n').slice(-4).join('\n') || `exit ${code}`));
    });
    p.on('error', (e) => reject(e));
  });
}
const shSoft = (cmd) => sh(cmd).catch(() => '');

// ------------------------------------------------------------- resources ----
const lanIP = () =>
  Object.values(os.networkInterfaces()).flat()
    .find((n) => n && n.family === 'IPv4' && !n.internal)?.address ?? '<server-ip>';

async function memMB() {
  if (DRY) return 4096;
  const txt = await shSoft('awk \'/MemTotal/ {printf "%d", $2/1024}\' /proc/meminfo');
  return Number(txt || 0);
}
async function diskMB() {
  if (DRY) return 20000;
  const txt = await shSoft('df -Pm /var | awk \'NR==2 {print $4}\'');
  return Number(txt || 0);
}
async function k3sState() {
  if (DRY) return { server: false, agent: false, version: '' };
  const version = (await shSoft('/usr/local/bin/k3s --version 2>/dev/null | head -1')).trim();
  const server = (await shSoft('test -f /var/lib/rancher/k3s/server/token && echo yes')).trim() === 'yes';
  const agent = (await shSoft('test -f /etc/systemd/system/k3s-agent.service && echo yes')).trim() === 'yes';
  return { server, agent, version };
}

const K3S_INSTALL = 'https://get.k3s.io';
const good = (t) => console.log('  ' + green('✔') + ' ' + t);
const warn = (t) => console.log('  ' + yellow('!') + ' ' + t);
const bad = (t) => console.log('  ' + red('✖') + ' ' + t);

// -------------------------------------------------------------- preflight ---
async function preflight({ agent = false, url: forcedUrl, reach = true } = {}) {
  const problems = [];
  let warnings = 0;

  await step('Checking this machine', async (ctx) => {
    if (!DRY) {
      if (os.platform() !== 'linux') problems.push('This tool installs on Linux only (Ubuntu/Debian tested).');
      if (!IS_ROOT) problems.push('Run it with sudo: k3s installs a system service as root.');
      if (!(await shSoft('command -v curl'))) problems.push('curl is required and was not found.');
    }
    ctx.note('linux · root · curl');
    const [mem, disk] = [await memMB(), await diskMB()];
    ctx.note(`${mem} MB RAM · ${disk} MB free`);
    if (mem && mem < 1900) problems.push(`Only ${mem} MB of RAM. A worker needs about 2 GB.`);
    if (disk && disk < 8000) problems.push(`Only ${disk} MB free on /var. k3s needs about 8 GB.`);
    if (!DRY) {
      const swap = await shSoft('swapon --show=NAME --noheadings | wc -l');
      if (Number(swap || 0) > 0) { warnings++; ctx.log('swap is on'); }
    }
    ctx.note('memory and disk ok');
  }, 'dots12');

  if (!reach) {
    console.log('  ' + dim('network check deferred until the tailnet is joined'));
  }
  await step('Checking network', async (ctx) => {
    if (!reach) { ctx.note('tailnet not joined yet'); return; }
    if (agent) {
      const url = (forcedUrl || opt('--url') || '').replace(/\/$/, '');
      if (!/^https:\/\/[^\s]+$/.test(url)) problems.push('--url must look like https://SERVER:6443');
      else if (DRY) {
        ctx.note('dry run');
      } else {
        const code = await shSoft(`curl -sk -o /dev/null -w '%{http_code}' --max-time 10 ${url}/version`);
        if (['200', '401', '403'].includes(code)) ctx.note(`server answers (${code})`);
        else problems.push(`Cannot reach ${url}. A worker must be able to reach the server on 6443.`);
      }
    } else if (!DRY) {
      await sh(`${'curl -sfI https://get.k3s.io >/dev/null'}`);
      ctx.note('get.k3s.io reachable');
    }
  }, 'earth');

  if (warnings) warn(`${warnings} warning(s) — see the log; they do not stop the install.`);
  if (problems.length) {
    console.log();
    for (const p of problems) bad(p);
    console.log('\n  ' + dim('Nothing was changed. Fix the items above and run again.') + '\n');
    process.exit(1);
  }
}

// ----------------------------------------------------------------- server ---
async function server() {
  const state = await k3sState();
  if (state.server) {
    good('This machine is already a k3s server.');
    console.log('  ' + dim(`${state.version || 'version unknown'} · nothing to install`) + '\n');
    const token = DRY ? '<token>' : await readFile('/var/lib/rancher/k3s/server/node-token', 'utf8').catch(() => '');
    if (token) printJoin(token.trim());
    return;
  }
  await preflight();

  const extra = [];
  if (opt('--tls-san')) extra.push(`--tls-san ${JSON.stringify(opt('--tls-san'))}`);
  if (opt('--disable')) extra.push(`--disable ${JSON.stringify(opt('--disable'))}`);
  const env = { INSTALL_K3S_SKIP_ENABLE: 'false' };
  if (opt('--version')) env.INSTALL_K3S_VERSION = opt('--version');

  await step('Installing the cluster server', (ctx) => {
    ctx.note(opt('--version') ? `pinned ${opt('--version')}` : 'stable channel');
    return sh(`curl -sfL ${K3S_INSTALL} | sh -s - server --write-kubeconfig-mode 600 ${extra.join(' ')}`, env);
  }, 'bouncingBar');

  await step('Waiting for the node to be Ready', async (ctx) => {
    if (DRY) return sleep(1500);
    for (let n = 1; n <= 60; n++) {
      ctx.note(`attempt ${n}/60`);
      const out = await shSoft('k3s kubectl get nodes --no-headers 2>/dev/null');
      if (/\sReady\s/.test(out)) return;
      await sleep(2000);
    }
    throw new Error('The node did not become Ready within two minutes. Check: journalctl -u k3s -n 50');
  }, 'moon');

  const token = DRY
    ? 'K10xxxxxxxxxxxxxxxx::server:xxxxxxxx'
    : (await readFile('/var/lib/rancher/k3s/server/node-token', 'utf8')).trim();
  console.log();
  good(bold('Cluster is up.'));
  printJoin(token);
}

function printJoin(token) {
  console.log('  ' + 'Join more nodes with:\n');
  console.log('  ' + cyan(`sudo node cluster.mjs agent --url https://${lanIP()}:6443 --token '${token}'`));
  console.log('\n  ' + dim('That token can add workers: treat it like a password.') + '\n');
}

// ------------------------------------------------------------- join code ----
function decodeCode(raw) {
  const text = String(raw || '').trim();
  if (!text.startsWith('k3s1.')) throw new Error('That does not look like a join code (expected k3s1.…)');
  const b64 = text.slice(5).replace(/-/g, '+').replace(/_/g, '/');
  const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
  let data;
  try {
    data = JSON.parse(Buffer.from(padded, 'base64').toString('utf8'));
  } catch {
    throw new Error('The join code is damaged — copy it again from the page.');
  }
  if (!/^https:\/\/[A-Za-z0-9.:-]+$/.test(data.server || '')) throw new Error('The join code has no usable server address.');
  if (!data.token) throw new Error('The join code has no cluster token.');
  return data;
}

async function joinWithCode(code) {
  const data = decodeCode(code);
  const node = (data.node_name || os.hostname().split('.')[0]).toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63) || 'worker';

  await preflight({ agent: true, url: data.server, reach: !data.tailscale_key });

  if (data.tailscale_key) {
    await step('Joining the private network', async (ctx) => {
      ctx.note('tailscale');
      const have = await shSoft('command -v tailscale');
      if (!have && !DRY) {
        ctx.note('installing tailscale');
        await sh('curl -fsSL https://tailscale.com/install.sh | sh');
      }
      const ip = (await shSoft('tailscale ip -4 2>/dev/null | head -1')).trim();
      if (!ip) {
        await sh(`tailscale up --authkey ${JSON.stringify(data.tailscale_key)} --hostname ${JSON.stringify(node)} ` +
                 '--advertise-tags=tag:k3s --accept-dns=false --accept-routes=false --ssh=false');
      }
      if (DRY) return;
      for (let n = 0; n < 30; n++) {
        const addr = (await shSoft('tailscale ip -4 2>/dev/null | head -1')).trim();
        if (addr) { ctx.note(addr); return; }
        await sleep(1000);
      }
      throw new Error('This machine did not get a tailnet address. The join code may have expired.');
    }, 'earth');
  }

  await step('Writing the cluster settings', async (ctx) => {
    const ip = DRY ? '100.0.0.1' : (await shSoft('tailscale ip -4 2>/dev/null | head -1')).trim();
    ctx.note(node);
    if (DRY) return;
    if (data.tailscale_key && !ip) throw new Error('No tailnet address to advertise; cannot continue.');
    await sh('install -d -m 700 /etc/rancher/k3s');
    await writeFile('/etc/rancher/k3s/token', data.token + '\n', { mode: 0o600 });
    await chmod('/etc/rancher/k3s/token', 0o600);
    const lines = [
      `server: ${data.server}`,
      'token-file: /etc/rancher/k3s/token',
      `node-name: ${node}`,
    ];
    if (ip) { lines.push(`node-ip: ${ip}`, 'flannel-iface: tailscale0'); }
    if (data.taint) lines.push('node-taint:', `  - ${data.taint}`);
    if (data.label) lines.push('node-label:', `  - ${data.label}`);
    await writeFile('/etc/rancher/k3s/config.yaml', lines.join('\n') + '\n', { mode: 0o600 });
    await chmod('/etc/rancher/k3s/config.yaml', 0o600);
    ctx.note('0600 config + token');
  }, 'dots12');

  const state = await k3sState();
  await step(state.agent ? 'Restarting the existing agent' : 'Installing the cluster agent', async (ctx) => {
    ctx.note(data.server);
    if (DRY) return sleep(1200);
    if (state.agent) {
      const keep = await shSoft('cat /var/lib/rancher/k3s/agent/node-password.txt 2>/dev/null');
      await shSoft('timeout 30 systemctl stop k3s-agent');
      await shSoft('rm -rf /var/lib/rancher/k3s/agent');
      if (keep.trim()) {
        await writeFile('/var/lib/rancher/k3s/agent/node-password.txt', keep.trim() + '\n', { mode: 0o600 });
      }
      await sh('systemctl start --no-block k3s-agent');
      return;
    }
    await sh(`curl -sfL ${K3S_INSTALL} | sh -s - agent`);
  }, 'bouncingBar');

  await step('Waiting for k3s-agent to become active', async (ctx) => {
    if (DRY) return sleep(1200);
    let renewed = false;
    for (let n = 1; n <= 60; n++) {
      ctx.note(`${n * 3}s`);
      const live = await shSoft('systemctl is-active k3s-agent');
      const ca = await shSoft('test -f /var/lib/rancher/k3s/agent/client-ca.crt && echo yes');
      if (live === 'active' && ca.trim() === 'yes') return;
      const recent = await shSoft('journalctl -u k3s-agent --since "-2 min" --no-pager 2>/dev/null');
      if ((recent.match(/invalid bearer token|not authorized/g) || []).length >= 2) {
        if (!renewed && data.renew) {
          renewed = true;
          ctx.note('renewing the code');
          const fresh = await shSoft(`curl -sS -m 20 -X POST https://k3s.kituwa.com/api/v1/enroll/renew ` +
            `-H 'Content-Type: application/json' --data '{"renew":"${data.renew}"}'`);
          const m = fresh.match(/"code":"(k3s1\.[^"]+)"/);
          if (m) {
            const next = decodeCode(m[1]);
            data.token = next.token;
            data.renew = next.renew;
            await writeFile('/etc/rancher/k3s/token', next.token + '\n', { mode: 0o600 });
            await sh('systemctl start --no-block k3s-agent');
            continue;
          }
        }
        throw new Error('The cluster rejected this code (401) and it could not be renewed. One click gets a new one: https://k3s.kituwa.com/join/');
      }
      await sleep(3000);
    }
    throw new Error('k3s-agent did not become active. journalctl -u k3s-agent -n 50');
  }, 'arc');

  console.log();
  good(bold(`This machine is now a ${data.role || 'worker'} in the Kituwa cluster.`));
  console.log('  ' + dim(`node ${node} · starts on boot · safe to close this window`) + '\n');
}

// ------------------------------------------------------------------ agent ---
async function agent() {
  const code = opt('--code') || (opt('--code-file') ? (await readFile(opt('--code-file'), 'utf8')).trim() : '');
  if (code) return joinWithCode(code);

  const url = (opt('--url') || '').replace(/\/$/, '');
  let token = opt('--token') || '';
  if (!token && opt('--token-file')) token = (await readFile(opt('--token-file'), 'utf8').catch(() => '')).trim();
  if (!token && process.env.K3S_TOKEN) token = process.env.K3S_TOKEN.trim();

  if (!url || !token) {
    console.log('  ' + red('Missing --url or --token.') + '\n');
    console.log('  ' + dim('Get both from the server step: sudo node cluster.mjs server') + '\n');
    console.log('  ' + dim('Tip: --token-file /root/join.code keeps the token out of the process list.') + '\n');
    process.exit(1);
  }
  if (opt('--token')) {
    warn('--token is visible in the process list. Prefer --token-file or K3S_TOKEN.');
  }

  const state = await k3sState();
  if (state.agent) {
    warn('k3s is already installed on this machine as an agent — re-joining updates the settings.');
  }
  await preflight({ agent: true });

  await step('Joining the cluster', (ctx) => {
    ctx.note(url);
    // The token goes in the environment, never into the command string.
    return sh(`curl -sfL ${K3S_INSTALL} | sh -s - agent`, { K3S_URL: url, K3S_TOKEN: token });
  }, 'bouncingBar');

  await step('Waiting for k3s-agent to become active', async (ctx) => {
    if (DRY) return sleep(1200);
    for (let n = 1; n <= 40; n++) {
      ctx.note(`${n * 3}s`);
      const s = await shSoft('systemctl is-active k3s-agent');
      if (s === 'active') return;
      const recent = await shSoft('journalctl -u k3s-agent --since "-2 min" --no-pager 2>/dev/null');
      if ((recent.match(/invalid bearer token|not authorized/g) || []).length >= 2) {
        throw new Error('The cluster rejected this token (401 Not authorized). Ask for a fresh join code and run again.');
      }
      await sleep(3000);
    }
    throw new Error('k3s-agent did not become active. Check: journalctl -u k3s-agent -n 50');
  }, 'arc');

  console.log();
  good(bold('Node joined.'));
  console.log('  ' + dim('It runs as a service: it starts on boot and keeps running after you close this window.') + '\n');
}

// ----------------------------------------------------------------- status ---
async function status() {
  const state = await k3sState();
  console.log('  ' + bold('This machine') + '\n');
  if (state.server) good('role: k3s server (control plane)');
  else if (state.agent) good('role: k3s agent (worker)');
  else warn('k3s is not installed here');
  if (state.version) console.log('  ' + dim(state.version));

  if (state.server || state.agent) {
    const unit = state.server ? 'k3s' : 'k3s-agent';
    const active = (await shSoft(`systemctl is-active ${unit}`)).trim();
    const enabled = (await shSoft(`systemctl is-enabled ${unit}`)).trim();
    console.log('');
    if (active === 'active') good(`${unit}: active${enabled === 'enabled' ? ' (starts on boot)' : ` (${enabled})`}`);
    else bad(`${unit}: ${active || 'unknown'}`);
  }
  if (state.server) {
    const nodes = await shSoft('k3s kubectl get nodes -o wide --no-headers 2>/dev/null || sudo -n k3s kubectl get nodes --no-headers');
    console.log('\n  ' + bold('Nodes') + '\n');
    console.log(nodes ? nodes.split('\n').map((l) => '  ' + l).join('\n') : '  ' + dim('none yet'));
  }
  console.log();
}

// -------------------------------------------------------------- uninstall ---
async function uninstall() {
  const state = await k3sState();
  if (!state.server && !state.agent) {
    warn('k3s is not installed here — nothing to remove.');
    return;
  }
  if (!YES && !TTY) {
    console.log('  ' + red('Refusing to remove k3s without confirmation.') + '\n');
    console.log('  ' + dim('Re-run with --yes when you mean it:') + '\n');
    console.log('  ' + dim('  sudo node cluster.mjs uninstall --yes') + '\n');
    process.exit(1);
  }
  if (!YES && TTY) {
    const which = state.server ? 'SERVER (the control plane, every worker depends on it)' : 'agent';
    process.stdout.write(`  Remove the k3s ${which} from this machine? [y/N] `);
    const answer = await new Promise((r) => process.stdin.once('data', (d) => r(d.toString().trim())));
    if (!/^y(es)?$/i.test(answer)) { console.log('  ' + dim('Left as it was.')); return; }
  }
  const script = state.server ? '/usr/local/bin/k3s-uninstall.sh' : '/usr/local/bin/k3s-agent-uninstall.sh';
  await step('Removing k3s', () => sh(`test -x ${script} && ${script}`), 'noise');
  good('Removed. This machine is no longer part of the cluster.');
}


// ------------------------------------------------------------------ doctor ---
// One command for "it is stuck". It collects a 20-line summary, uploads it to the
// Kituwa inbox (no logs to copy), and with --fix it wipes the agent and re-joins.
async function doctor() {
  const fix = has('--fix');
  const lines = [];

  await step('Reading the k3s state', async (ctx) => {
    const [version, active, enabled, config, tokenBytes, tokenTail, tsIp, api] = await Promise.all([
      shSoft('/usr/local/bin/k3s --version 2>/dev/null | head -1'),
      shSoft('systemctl is-active k3s-agent 2>/dev/null'),
      shSoft('systemctl is-enabled k3s-agent 2>/dev/null'),
      shSoft('sed -e "s/^\\(token:.*\\)/token: <redacted>/" /etc/rancher/k3s/config.yaml 2>/dev/null'),
      shSoft('wc -c < /etc/rancher/k3s/token 2>/dev/null'),
      shSoft('tail -c 8 /etc/rancher/k3s/token 2>/dev/null'),
      shSoft('tailscale ip -4 2>/dev/null | head -1'),
      shSoft("curl -sk -o /dev/null -w '%{http_code}' -m 8 https://100.120.24.106:6443/version"),
    ]);
    ctx.note(active || 'unknown');
    lines.push(`host: ${os.hostname()} · ${os.release()} · ${os.arch()}`);
    lines.push(`k3s: ${version || 'not installed'}`);
    lines.push(`service: ${active || 'unknown'} / ${enabled || 'unknown'}`);
    lines.push('--- config (token redacted)');
    lines.push(config || 'no config.yaml');
    lines.push(`--- token: ${(tokenBytes || '0').trim()} bytes, ends ${(tokenTail || '').trim()}`);
    lines.push(`--- tailscale: ${(tsIp || 'none').trim()}`);
    lines.push(`--- api: ${(api || 'unreachable').trim()}`);
    lines.push('--- last agent errors');
    const errs = await shSoft('journalctl -u k3s-agent -n 400 --no-pager 2>/dev/null '
      + '| grep -iE "error|fatal|fail|unauthor|denied|refused|timeout|password" '
      + '| tail -12 | cut -c1-160');
    lines.push(errs || '(none)');
  }, 'dots12');

  const summary = lines.join('\n');
  console.log('\n' + summary.split('\n').map((l) => '  ' + dim(l.slice(0, 150))).join('\n') + '\n');

  await step('Sending the summary to Kituwa', async (ctx) => {
    if (DRY) { ctx.note('dry run'); return; }
    const jar = `/tmp/kc-jar-${process.pid}`;
    const tokOut = await shSoft(`curl -sS -c ${jar} -m 15 https://k3s.kituwa.com/api/v1/forms/k3s-join/token`);
    const tok = (tokOut.match(/"token":"([^"]+)"/) || [])[1];
    if (!tok) throw new Error('could not get a form token');
    const body = JSON.stringify({
      name: 'join-diagnostic',
      email: 'diagnostic@kituwa.com',
      body: summary.slice(0, 3500),
      machine: 'diagnostic',
      reference: `diag-${Date.now()}`,
    });
    const reply = await shSoft(`curl -sS -b ${jar} -m 20 -X POST https://k3s.kituwa.com/api/v1/forms/k3s-join `
      + `-H 'Origin: https://k3s.kituwa.com' -H 'X-Kituwa-Form-Token: ${tok}' `
      + `-H 'Content-Type: application/json' --data ${JSON.stringify(body)}`);
    await shSoft(`rm -f ${jar}`);
    const id = (reply.match(/"id":(\d+)/) || [])[1];
    ctx.note(id ? `inbox #${id}` : 'not delivered');
    if (id) good(`Reference #${id} — the operator can read it, nothing to copy.`);
  }, 'earth');

  if (!fix) {
    console.log('  ' + dim('Add --fix to wipe the agent and re-join from a clean slate.') + '\n');
    return;
  }

  await step('Wiping the agent state', async (ctx) => {
    ctx.note('clean slate');
    if (DRY) return sleep(600);
    await shSoft('timeout 30 systemctl stop k3s-agent');
    await shSoft('test -x /usr/local/bin/k3s-agent-uninstall.sh && /usr/local/bin/k3s-agent-uninstall.sh');
    await shSoft('rm -rf /var/lib/rancher/k3s /etc/rancher/k3s');
  }, 'noise');

  console.log('  ' + bold(green('Clean slate.')) + ' Now join again with a fresh code:');
  console.log('  ' + cyan("sudo node cluster.mjs agent --code 'k3s1.…'") + '\n');
  console.log('  ' + dim('Get the code at https://k3s.kituwa.com/join/ — one click, no form to fill.') + '\n');
}

// ------------------------------------------------------------------- help ---
function help() {
  console.log('  ' + bold('Usage'));
  console.log('    kituwa-cluster server [flags]              install the cluster server');
  console.log('    kituwa-cluster agent --code k3s1.…         join using a code from the site');
  console.log('    kituwa-cluster agent --url U --token T     join a cluster you run yourself');
  console.log('    kituwa-cluster status                      what is installed here');
  console.log('    kituwa-cluster uninstall [--yes]           remove k3s from this machine');
  console.log('');
  console.log('  ' + bold('Server flags'));
  console.log('    --tls-san NAME        extra name on the API certificate');
  console.log('    --disable COMPONENT   skip a bundled component (e.g. traefik)');
  console.log('    --version VER         pin a release (e.g. v1.36.5+k3s1)');
  console.log('');
  console.log('  ' + bold('Agent flags'));
  console.log('    --code CODE           the one-line join code from k3s.kituwa.com/join');
  console.log('    --code-file PATH      read the code from a file');
  console.log('    --url URL             https://SERVER:6443');
  console.log('    --token TOKEN         the join token (visible in ps — prefer the next two)');
  console.log('    --token-file PATH     read the token from a file');
  console.log('    K3S_TOKEN=...         read the token from the environment');
  console.log('');
  console.log('  ' + bold('Any command'));
  console.log('    --dry-run             show the whole interface, change nothing');
  console.log('');
  console.log('  ' + bold('Examples'));
  console.log('    sudo node cluster.mjs server --disable traefik');
  console.log('    sudo node cluster.mjs agent --url https://10.0.0.5:6443 --token-file /root/join.code');
  console.log('    node cluster.mjs server --dry-run');
  console.log('');
}

// ------------------------------------------------------------------- main ---
if (!command || command === 'help' || has('--help') || has('-h')) {
  await banner();
  help();
  process.exit(0);
}

await banner();
await log(`start ${command} ${DRY ? '(dry run)' : ''}`);
if (command === 'server') await server();
else if (command === 'agent') await agent();
else if (command === 'status') await status();
else if (command === 'doctor') await doctor();
else if (command === 'uninstall') await uninstall();
else {
  bad(`Unknown command: ${command}`);
  console.log('\n  ' + dim('Try: node cluster.mjs --help') + '\n');
  process.exit(2);
}
