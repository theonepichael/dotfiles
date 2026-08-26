#!/usr/bin/env node
/**
 * agy elapsed-time status line
 *
 * Shows: ⏱ Xm Ys · ↓ Nk tokens · Model  (while running)
 *        ✓ Xm Ys · done HH:MM            (8s after finishing)
 *
 * Wire up inside agy: /statusline node ~/.claude/hooks/agy-elapsed.js
 * Or set in ~/.gemini/antigravity-cli/settings.json:
 *   "statusLine": { "command": "node ~/.claude/hooks/agy-elapsed.js", "stackWithDefault": true }
 *
 * Set AGY_STATUSLINE_DEBUG=1 to log the raw payload to /tmp/agy-statusline-debug.log
 */

const fs = require('fs');
const os = require('os');
const STATE_FILE = `${os.tmpdir()}/agy-elapsed-state.json`;

function fmtDuration(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

function fmtTokens(n) {
  if (!n || n === 0) return null;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k tokens`;
  return `${n} tokens`;
}

function fmtResetIn(seconds) {
  if (!seconds || seconds <= 0) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

function fmtQuota(quota, modelName) {
  if (!quota || typeof quota !== 'object') return null;
  const rawName = (modelName || '').trim();
  const m = rawName.toLowerCase();
  const isGemini = m.includes('gemini');
  const prefix = isGemini ? 'gemini-' : '3p-';

  // Dynamically derive label from modelName if non-Gemini
  let label = 'Gemini';
  if (!isGemini) {
    // Take the first word of model name (e.g., "Claude", "GPT", "DeepSeek", "Mistral", "o3-mini" -> "o3")
    const firstWord = rawName.split(/\s+/)[0] || '3p';
    label = firstWord.replace(/[^a-zA-Z0-9-]/g, '');
  }

  // Find the tightest (lowest remaining_fraction = highest usage) bucket for this provider
  const buckets = Object.entries(quota)
    .filter(([k]) => k.startsWith(prefix))
    .sort(([, a], [, b]) => a.remaining_fraction - b.remaining_fraction);
  if (!buckets.length) return null;
  const [, tightest] = buckets[0];
  const usedPct = Math.round((1 - tightest.remaining_fraction) * 100);
  const reset = fmtResetIn(tightest.reset_in_seconds);
  return reset ? `${label} ${usedPct}% used (resets in ${reset})` : `${label} ${usedPct}% used`;
}

function loadState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); }
  catch (_) { return {}; }
}

function saveState(s) {
  try { fs.writeFileSync(STATE_FILE, JSON.stringify(s)); }
  catch (_) {}
}

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', () => {
  let data = {};
  try { data = JSON.parse(raw); } catch (_) {}

  if (process.env.AGY_STATUSLINE_DEBUG) {
    fs.appendFileSync('/tmp/agy-statusline-debug.log',
      new Date().toISOString() + '\n' + JSON.stringify(data, null, 2) + '\n---\n');
  }

  const now = Date.now();
  // Real agy data model: agent_state = "working" | "idle" | "done"
  const agentState = (data.agent_state || '').toLowerCase();
  const isRunning = agentState === 'working';
  const isThinking = isRunning && (data.model?.id || '').toLowerCase().includes('thinking');
  // Tokens: sum of input+output from context_window
  const ctxWindow = data.context_window || {};
  const totalTokens = (ctxWindow.total_input_tokens || 0) + (ctxWindow.total_output_tokens || 0);
  // Model: object with display_name
  const modelName = (data.model?.display_name || data.model?.id || '');
  const state = loadState();
  let text = '';

  if (isRunning) {
    if (!state.turnStart) {
      state.turnStart = now;
      state.lastIdle = false;
      saveState(state);
    }
    const elapsed = now - state.turnStart;
    const icon = isThinking ? '🤔' : '⏱';
    const parts = [`${icon} ${fmtDuration(elapsed)}`];
    const tok = fmtTokens(totalTokens);
    if (tok) parts.push(`↓ ${tok}`);
    if (modelName) {
      const m = modelName
        .replace(/\s*\(Thinking\)/i, '')
        .replace(/^(Claude|Gemini)\s+/i, '')
        .trim();
      if (m) parts.push(m);
    }
    const quota = fmtQuota(data.quota, modelName);
    if (quota) parts.push(quota);
    text = parts.join(' · ');

  } else {
    if (state.turnStart && !state.lastIdle) {
      const elapsed = now - state.turnStart;
      const finishedAt = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
      const quota = fmtQuota(data.quota, modelName);
      text = `✓ ${fmtDuration(elapsed)} · done ${finishedAt}${quota ? ' · ' + quota : ''}`;
      state.lastIdle = true;
      state.doneAt = now;
      state.doneText = text;
      saveState(state);
    } else if (state.lastIdle && state.doneText) {
      // Recompute quota dynamically so percentage and reset time update live during idle
      const quota = fmtQuota(data.quota, modelName);
      const donePrefix = state.doneText.split(' · ').slice(0, 2).join(' · ');
      text = `${donePrefix}${quota ? ' · ' + quota : ''}`;
    } else {
      // If no recent turn recorded yet in state, construct a baseline idle statusline
      const parts = [];
      if (modelName) {
        const m = modelName
          .replace(/\s*\(Thinking\)/i, '')
          .replace(/^(Claude|Gemini)\s+/i, '')
          .trim();
        if (m) parts.push(m);
      }
      const quota = fmtQuota(data.quota, modelName);
      if (quota) parts.push(quota);
      text = parts.length ? parts.join(' · ') : '';
    }
  }

  process.stdout.write(text + '\n');
});
