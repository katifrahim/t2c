#!/usr/bin/env node
'use strict';

// ANSI colors
const RESET  = '\x1b[0m';
const BOLD   = '\x1b[1m';
const DIM    = '\x1b[37m'; // reset-time values: off-white (readable on dark terminals)
const CYAN   = '\x1b[96m';
const WHITE  = '\x1b[97m';
const GRAY   = '\x1b[97m'; // labels/separators: bright white for readability on dark terminals
const DGRAY  = '\x1b[37m'; // empty progress-bar shading: off-white (readable on dark terminals)
const GREEN  = '\x1b[92m';
const YELLOW = '\x1b[93m';
const RED    = '\x1b[91m';

const SEP = ` ${GRAY}|${RESET} `;

function colorForPct(pct) {
  if (pct < 50) return GREEN;
  if (pct < 80) return YELLOW;
  return RED;
}

function progressBar(pct, width = 10) {
  const filled = Math.floor(pct * width / 100);
  const empty  = width - filled;
  const col = colorForPct(pct);
  return `${col}${'█'.repeat(filled)}${DGRAY}${'░'.repeat(empty)}${RESET}`;
}

function fmtTime(epoch) {
  const d = new Date(epoch * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}

function fmtDate(epoch) {
  const d = new Date(epoch * 1000);
  const days   = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${days[d.getDay()]} ${months[d.getMonth()]} ${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
}

// Read stdin synchronously via fd 0 (works on macOS, Windows, Git Bash, and WSL)
let raw = '';
try {
  const buf = Buffer.alloc(65536);
  let n;
  while ((n = require('fs').readSync(0, buf, 0, buf.length)) > 0) {
    raw += buf.slice(0, n).toString('utf8');
  }
} catch (_) {}

let data = {};
try { data = JSON.parse(raw); } catch (_) {}

// Model
const modelObj = data.model || {};
const model = modelObj.display_name || modelObj.id || 'Claude';

// Folder
const cwd = (data.workspace && data.workspace.current_dir) || data.cwd || '';
const folder = cwd ? cwd.replace(/\\/g, '/').replace(/\/$/, '').split('/').pop() : '?';

// Context bar
const ctxWin = data.context_window || {};
const usedPct = ctxWin.used_percentage;
let ctxStr;
if (usedPct != null) {
  const p = Math.floor(usedPct);
  ctxStr = `${GRAY}ctx: ${RESET}${progressBar(p)} ${colorForPct(p)}${BOLD}${p}%${RESET}`;
} else {
  ctxStr = `${GRAY}ctx: ${DGRAY}${'░'.repeat(10)}${GRAY} --${RESET}`;
}

// 5-hour rate limit
const rateLimits = data.rate_limits || {};
const five = rateLimits.five_hour || {};
let fiveStr;
if (five.used_percentage != null) {
  const p = Math.floor(five.used_percentage);
  const rstPart = five.resets_at
    ? ` ${GRAY}rst: ${DIM}${fmtTime(five.resets_at)}${RESET}`
    : '';
  fiveStr = `${GRAY}5h: ${RESET}${progressBar(p)} ${colorForPct(p)}${BOLD}${p}%${RESET}${rstPart}`;
} else {
  fiveStr = `${GRAY}5h: ${DGRAY}${'░'.repeat(10)}${GRAY} --${RESET}`;
}

// 7-day rate limit
const week = rateLimits.seven_day || {};
let weekStr;
if (week.used_percentage != null) {
  const p = Math.floor(week.used_percentage);
  const rstPart = week.resets_at
    ? ` ${GRAY}rst: ${DIM}${fmtDate(week.resets_at)}${RESET}`
    : '';
  weekStr = `${GRAY}7d: ${RESET}${progressBar(p)} ${colorForPct(p)}${BOLD}${p}%${RESET}${rstPart}`;
} else {
  weekStr = `${GRAY}7d: ${DGRAY}${'░'.repeat(10)}${GRAY} --${RESET}`;
}

process.stdout.write(
  `${CYAN}${BOLD}${model}${RESET}${SEP}${WHITE}${folder}${RESET}${SEP}${ctxStr}${SEP}${fiveStr}${SEP}${weekStr}`
);
