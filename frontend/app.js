/**
 * LC Gate Status Checker - Minimal Frontend Client
 *
 * Strict Constraints:
 * - Single page layout with lightweight settings modal.
 * - No auto-refresh or background polling.
 * - Data calls strictly on user demand:
 *   - On load: GET /gate
 *   - On status check click: GET /gate/status
 *   - On settings modal open: GET /settings
 *   - On settings save click: POST /settings
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

  // Settings Modal Elements
  const settingsBtn = document.getElementById('settings-btn');
  const settingsModal = document.getElementById('settings-modal');
  const modalCloseBtn = document.getElementById('modal-close-btn');
  const settingsCancelBtn = document.getElementById('settings-cancel-btn');
  const settingsSaveBtn = document.getElementById('settings-save-btn');
  const futureSlider = document.getElementById('future-slider');
  const pastSlider = document.getElementById('past-slider');
  const mergeSlider = document.getElementById('merge-slider');
  const futureValBadge = document.getElementById('future-val-badge');
  const pastValBadge = document.getElementById('past-val-badge');
  const mergeValBadge = document.getElementById('merge-val-badge');
  const settingsFeedback = document.getElementById('settings-feedback');

  // Load initial gate metadata on startup (no polling)
  loadGateConfig();

  // Click handler for status check
  checkBtn.addEventListener('click', () => {
    checkGateStatus();
  });

  // Settings Modal Listeners
  settingsBtn.addEventListener('click', () => {
    openSettingsModal();
  });

  modalCloseBtn.addEventListener('click', () => {
    closeSettingsModal();
  });

  settingsCancelBtn.addEventListener('click', () => {
    closeSettingsModal();
  });

  settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) {
      closeSettingsModal();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !settingsModal.classList.contains('hidden')) {
      closeSettingsModal();
    }
  });

  // Real-time slider value updates on 'input' event
  futureSlider.addEventListener('input', () => {
    futureValBadge.textContent = `${futureSlider.value} min`;
  });

  pastSlider.addEventListener('input', () => {
    pastValBadge.textContent = `${pastSlider.value} min`;
  });

  if (mergeSlider) {
    mergeSlider.addEventListener('input', () => {
      mergeValBadge.textContent = `${mergeSlider.value} min`;
    });
  }

  // Save Settings handler
  settingsSaveBtn.addEventListener('click', () => {
    saveSettings();
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

    // Read closure window from backend response (backend is single source of truth)
    const pastMin = data.closure_window_past_min !== undefined ? data.closure_window_past_min : 3;
    const futureMin = data.closure_window_future_min !== undefined ? data.closure_window_future_min : 2;

    // 1. Render Status Badge & Contextual Message
    if (status === 'likely_open') {
      statusBadgeEl.className = 'status-badge badge-open';
      badgeTextEl.textContent = 'Likely Open';
      statusMessageEl.textContent = `${representsText} is estimated to be open. No trains are in the crossing window (-${pastMin} to +${futureMin} min).`;
    } else if (status === 'likely_closed') {
      statusBadgeEl.className = 'status-badge badge-closed';
      badgeTextEl.textContent = 'Likely Closed';

      // Check if closure is part of a merged continuous interval
      const closedIntervals = Array.isArray(data.closed_intervals) ? data.closed_intervals : [];
      const evalTime = data.evaluated_at ? new Date(data.evaluated_at) : new Date();

      let activeMergedGroup = null;
      for (const group of closedIntervals) {
        if (group.is_merged) {
          const gStart = new Date(group.interval_start);
          const gEnd = new Date(group.interval_end);
          if (evalTime >= gStart && evalTime <= gEnd) {
            activeMergedGroup = group;
            break;
          }
        }
      }

      // Fallback: if check falls slightly outside due to clock resolution but a merged group exists
      if (!activeMergedGroup) {
        for (const group of closedIntervals) {
          if (group.is_merged) {
            activeMergedGroup = group;
            break;
          }
        }
      }

      if (activeMergedGroup) {
        let untilTimeStr = '--:--';
        if (activeMergedGroup.interval_end) {
          try {
            const endDate = new Date(activeMergedGroup.interval_end);
            untilTimeStr = endDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
          } catch (_) {
            untilTimeStr = activeMergedGroup.interval_end;
          }
        }

        const trainNumSet = new Set((activeMergedGroup.train_numbers || []).map(String));
        const matchingTrains = trains.filter((t) => trainNumSet.has(String(t.train_number)));
        const trainLabels = matchingTrains.length > 0
          ? matchingTrains.map((t) => `${t.train_name || 'Train'} #${t.train_number}`)
          : (activeMergedGroup.train_numbers || []).map((n) => `#${n}`);

        const trainCount = activeMergedGroup.train_numbers ? activeMergedGroup.train_numbers.length : trainLabels.length;
        const trainListText = trainLabels.join(', ');

        statusMessageEl.textContent = `${representsText} is estimated to be closed \u2014 ${trainCount} closely-spaced trains (${trainListText}) mean the gate is expected to stay down continuously until ${untilTimeStr}.`;
      } else {
        statusMessageEl.textContent = `${representsText} is estimated to be closed due to an approaching or recent train transit.`;
      }
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

        let mergedTag = '';
        if (Array.isArray(t.in_merged_group_with) && t.in_merged_group_with.length > 0) {
          mergedTag = ` &bull; <span style="color: #58a6ff;">Continuous with #${escapeHtml(t.in_merged_group_with.join(', #'))}</span>`;
        }

        li.innerHTML = `
          <div class="train-item-info">
            <span class="train-name-num">${escapeHtml(t.train_name || 'Train')} #${escapeHtml(t.train_number || '')}</span>
            <span class="train-delay">${escapeHtml(delayText)} &bull; Sched: ${escapeHtml(t.scheduled_time || '')}${mergedTag}</span>
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

  async function openSettingsModal() {
    clearSettingsFeedback();
    settingsSaveBtn.disabled = true;
    settingsSaveBtn.textContent = 'Loading...';
    settingsModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    try {
      const res = await fetch('/settings');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const s = await res.json();

      futureSlider.value = s.closure_window_future_min ?? 7;
      pastSlider.value = s.closure_window_past_min ?? 2;
      futureValBadge.textContent = `${futureSlider.value} min`;
      pastValBadge.textContent = `${pastSlider.value} min`;

      if (mergeSlider) {
        mergeSlider.value = s.train_merge_threshold_min ?? 10;
        mergeValBadge.textContent = `${mergeSlider.value} min`;
      }
    } catch (err) {
      showSettingsFeedback(`Could not load settings from server: ${err.message}`, 'error');
    } finally {
      settingsSaveBtn.disabled = false;
      settingsSaveBtn.textContent = 'Save';
    }
  }

  function closeSettingsModal() {
    settingsModal.classList.add('hidden');
    document.body.style.overflow = '';
    clearSettingsFeedback();
  }

  async function saveSettings() {
    const futureVal = parseInt(futureSlider.value, 10);
    const pastVal = parseInt(pastSlider.value, 10);
    const mergeVal = mergeSlider ? parseInt(mergeSlider.value, 10) : 10;

    settingsSaveBtn.disabled = true;
    settingsSaveBtn.textContent = 'Saving...';
    clearSettingsFeedback();

    try {
      const res = await fetch('/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          closure_window_future_min: futureVal,
          closure_window_past_min: pastVal,
          train_merge_threshold_min: mergeVal,
        }),
      });

      if (!res.ok) {
        let errMessage = `HTTP ${res.status}`;
        try {
          const errData = await res.json();
          if (errData.detail) errMessage = errData.detail;
        } catch (_) {}
        throw new Error(errMessage);
      }

      showSettingsFeedback('Settings saved successfully.', 'success');
      setTimeout(() => {
        closeSettingsModal();
        settingsSaveBtn.disabled = false;
        settingsSaveBtn.textContent = 'Save';
      }, 500);
    } catch (err) {
      showSettingsFeedback(err.message, 'error');
      settingsSaveBtn.disabled = false;
      settingsSaveBtn.textContent = 'Save';
    }
  }

  function showSettingsFeedback(msg, type) {
    settingsFeedback.textContent = msg;
    settingsFeedback.className = `settings-feedback ${type === 'error' ? 'feedback-error' : 'feedback-success'}`;
    settingsFeedback.classList.remove('hidden');
  }

  function clearSettingsFeedback() {
    settingsFeedback.textContent = '';
    settingsFeedback.className = 'settings-feedback hidden';
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
