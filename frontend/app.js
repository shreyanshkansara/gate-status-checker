/**
 * LC Gate Status Checker - Minimal Frontend Client
 *
 * Strict Constraints:
 * - Single card layout.
 * - No auto-refresh or background polling.
 * - Data calls strictly on load (GET /gate) and on click (GET /gate/status).
 */

document.addEventListener('DOMContentLoaded', () => {
  const gateTitleEl = document.getElementById('gate-title');
  const gateSubtitleEl = document.getElementById('gate-subtitle');
  const statusBadgeEl = document.getElementById('status-badge');
  const badgeTextEl = document.getElementById('badge-text');
  const estimateNoteEl = document.getElementById('estimate-note');
  const statusMessageEl = document.getElementById('status-message');
  const trainListWrapEl = document.getElementById('train-list-wrap');
  const trainListEl = document.getElementById('train-list');
  const checkBtn = document.getElementById('check-btn');
  const btnSpinner = document.getElementById('btn-spinner');
  const btnText = document.getElementById('btn-text');
  const lastCheckedEl = document.getElementById('last-checked');

  // Load initial gate metadata on startup (no polling)
  loadGateConfig();

  // Click handler for status check
  checkBtn.addEventListener('click', () => {
    checkGateStatus();
  });

  async function loadGateConfig() {
    try {
      const res = await fetch('/gate');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const gate = await res.json();

      if (gate.name) {
        gateTitleEl.textContent = gate.name;
      }
      if (gate.represents && Array.isArray(gate.represents)) {
        gateSubtitleEl.textContent = `Represents ${gate.represents.join(' & ')} (single status unit)`;
      }
    } catch (err) {
      console.warn('Could not load initial gate config:', err);
    }
  }

  async function checkGateStatus() {
    // Set loading state
    checkBtn.disabled = true;
    btnSpinner.classList.remove('hidden');
    btnText.textContent = 'Checking...';

    statusBadgeEl.className = 'status-badge badge-neutral';
    badgeTextEl.textContent = 'Checking Status...';
    statusMessageEl.textContent = 'Contacting server and evaluating live train positions...';

    try {
      const res = await fetch('/gate/status');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      renderStatus(data);
      updateLastCheckedTimestamp();
    } catch (err) {
      statusBadgeEl.className = 'status-badge badge-warning';
      badgeTextEl.textContent = 'Check Failed';
      estimateNoteEl.classList.add('hidden');
      trainListWrapEl.classList.add('hidden');
      statusMessageEl.textContent = `Could not reach server: ${err.message}`;
    } finally {
      checkBtn.disabled = false;
      btnSpinner.classList.add('hidden');
      btnText.textContent = 'Check Status';
    }
  }

  function renderStatus(data) {
    const status = data.status || 'status_unknown';
    const trains = Array.isArray(data.trains) ? data.trains : [];
    const representsList = data.represents || ['Gate No. 30', 'Gate No. 31'];
    const representsText = representsList.join(' & ');

    // 1. Render Status Badge
    if (status === 'likely_open') {
      statusBadgeEl.className = 'status-badge badge-open';
      badgeTextEl.textContent = 'Likely Open';
      statusMessageEl.textContent = `${representsText} is estimated to be open. No trains are in the crossing window (-7 to +3 min).`;
    } else if (status === 'likely_closed') {
      statusBadgeEl.className = 'status-badge badge-closed';
      badgeTextEl.textContent = 'Likely Closed';
      statusMessageEl.textContent = `${representsText} is estimated to be closed due to an approaching or recent train transit.`;
    } else if (status === 'schedule_stale') {
      statusBadgeEl.className = 'status-badge badge-warning';
      badgeTextEl.textContent = 'Schedule Stale';
      statusMessageEl.innerHTML = `${data.error || 'Cached timetable is older than 14 days.'}<br><small style="color: var(--text-muted);">${data.instructions || ''}</small>`;
    } else {
      statusBadgeEl.className = 'status-badge badge-warning';
      badgeTextEl.textContent = 'Status Unknown';
      statusMessageEl.textContent = data.error || 'Could not determine live train status.';
    }

    // 2. Render Visual Estimate Note under badge
    if (data.distance_is_estimated) {
      estimateNoteEl.textContent = 'ETA is approximate \u2014 gate distance is a visual estimate, not a measured value.';
      estimateNoteEl.classList.remove('hidden');
    } else {
      estimateNoteEl.classList.add('hidden');
    }

    // 3. Render Supporting Train List
    if (trains.length > 0) {
      trainListEl.innerHTML = '';
      trains.forEach((t) => {
        const li = document.createElement('li');
        li.className = `train-item ${t.causes_closure ? 'causes-closure' : ''}`;

        let delayText = 'On Time';
        if (t.delay_minutes > 0) {
          delayText = `+${t.delay_minutes}m delay`;
        } else if (t.delay_minutes < 0) {
          delayText = `${t.delay_minutes}m early`;
        }
        if (t.live_status && t.live_status !== 'running') {
          delayText += ` (${t.live_status})`;
        }

        // Format ETA time
        let etaDisplay = '--:--';
        if (t.eta_at_gate) {
          try {
            const etaDate = new Date(t.eta_at_gate);
            etaDisplay = etaDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
          } catch (e) {
            etaDisplay = t.eta_at_gate;
          }
        }

        const minsFromNowText = t.minutes_from_now !== undefined
          ? (t.minutes_from_now >= 0 ? `in ${t.minutes_from_now}m` : `${Math.abs(t.minutes_from_now)}m ago`)
          : '';

        li.innerHTML = `
          <div class="train-item-info">
            <span class="train-name-num">${escapeHtml(t.train_name || 'Train')} #${escapeHtml(t.train_number || '')}</span>
            <span class="train-delay">${escapeHtml(delayText)} &bull; Sched: ${escapeHtml(t.scheduled_time || '')}</span>
          </div>
          <div class="train-eta-box">
            <div class="train-eta">ETA ${escapeHtml(etaDisplay)}</div>
            <div class="train-mins">${escapeHtml(minsFromNowText)}</div>
          </div>
        `;
        trainListEl.appendChild(li);
      });
      trainListWrapEl.classList.remove('hidden');
    } else {
      trainListWrapEl.classList.add('hidden');
    }
  }

  function updateLastCheckedTimestamp() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    lastCheckedEl.textContent = `Last checked at ${timeStr}`;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
});
