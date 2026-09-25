/* Forkcast interactions — plain JS, no framework. Each block only runs on
   pages that carry its data-* hook. Every form still submits normally
   without JS; this just adds the stepped flows, previews and polling. */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function toast(message) {
    const el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 1800);
  }
  $$(".toast[data-autohide]").forEach(el => setTimeout(() => el.remove(), 2200));

  // ── Dialogs and sheets ──────────────────────────────────────────────────
  let lastOpener = null;
  function openDialog(id, opener) {
    const d = document.getElementById(id);
    if (!d) return;
    lastOpener = opener || null;
    d.hidden = false;
    const focusable = d.querySelector("button, [href], input, select");
    if (focusable) focusable.focus();
  }
  function closeDialog(d) {
    d.hidden = true;
    if (lastOpener) lastOpener.focus();
  }
  document.addEventListener("click", e => {
    const opener = e.target.closest("[data-open-dialog]");
    if (opener) { openDialog(opener.dataset.openDialog, opener); return; }
    const closer = e.target.closest("[data-close-dialog]");
    if (closer) { closeDialog(closer.closest(".dialog-backdrop, .sheet-backdrop")); return; }
    // Tapping the dimmed area outside a sheet/dialog closes it.
    if (e.target.matches(".dialog-backdrop, .sheet-backdrop")) closeDialog(e.target);
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    $$(".dialog-backdrop:not([hidden]), .sheet-backdrop:not([hidden])").forEach(closeDialog);
  });

  // ── Rating cards (This week sheet + check-in step) ─────────────────────
  document.addEventListener("click", e => {
    const add = e.target.closest("[data-add-note]");
    if (!add) return;
    const note = $("[data-note]", add.closest("[data-rate-card]"));
    add.hidden = true;
    note.hidden = false;
    note.focus();
  });

  function ratedCards(root) {
    return $$("[data-rate-card]", root).filter(c => $("input[type=radio]:checked", c));
  }
  function syncRatingButton(root) {
    const btn = $("[data-save-ratings][data-then-next]", root);
    if (btn) btn.textContent = ratedCards(root).length ? "Save and continue" : "Skip";
  }
  document.addEventListener("change", e => {
    const card = e.target.closest("[data-rate-card]");
    if (card) syncRatingButton(card.closest("[data-step], .sheet"));
  });

  async function saveRatings(root) {
    const cards = ratedCards(root);
    for (const card of cards) {
      const body = new URLSearchParams();
      body.set("response", $("input[type=radio]:checked", card).value);
      body.set("note", ($("[data-note]", card).value || "").trim());
      const res = await fetch(`/feedback/${card.dataset.mealId}`, {
        method: "POST", body, headers: { Accept: "application/json" }, credentials: "same-origin",
      });
      if (!res.ok) throw new Error("rating failed");
      card.remove();
    }
    return cards.length;
  }

  // ── Upload screen ───────────────────────────────────────────────────────
  const upload = $("[data-upload]");
  if (upload) {
    const form = $("[data-upload-form]", upload);
    const fileInput = $("[data-file-input]", upload);
    const submit = $("[data-upload-submit]", upload);
    const later = $("[data-upload-later]", upload);
    const errorBox = $("[data-upload-error]", upload);
    const zone = $("[data-dropzone]", upload);
    let replacing = false;
    let previewUrl = null;

    const selectedWeek = () => $("input[name=week_start]:checked", form);
    function showError(message) {
      errorBox.hidden = !message;
      $("span", errorBox).textContent = message || "";
    }
    function render() {
      const week = selectedWeek();
      const already = week && week.dataset.uploaded === "true" && !replacing;
      const file = fileInput.files[0];
      $("[data-state=already]", upload).hidden = !already;
      $("[data-state=pick]", upload).hidden = already || !!file;
      $("[data-state=file]", upload).hidden = already || !file;
      $("[data-week-label]", upload).textContent = week ? week.dataset.label : "";
      if (later) later.hidden = already;
      submit.textContent = already ? "Continue" : "Upload menu";
      submit.disabled = !already && !file;
      if (file) {
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(file);
        $("[data-thumb]", upload).src = previewUrl;
        $("[data-file-name]", upload).textContent = file.name;
      }
    }

    fileInput.addEventListener("change", () => {
      const file = fileInput.files[0];
      if (file && !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
        fileInput.value = "";
        showError("That file type isn't supported — upload a JPEG, PNG or WEBP photo.");
      } else if (file && file.size > 10 * 1024 * 1024) {
        fileInput.value = "";
        showError("That photo is larger than 10 MB — please upload a smaller one.");
      } else {
        showError("");
      }
      render();
    });
    $("[data-remove-file]", upload).addEventListener("click", () => { fileInput.value = ""; render(); });
    $$("input[name=week_start]", form).forEach(r => r.addEventListener("change", () => { replacing = false; render(); }));
    $("[data-replace-confirm]", upload).addEventListener("click", () => {
      replacing = true;
      closeDialog(document.getElementById("replace-dialog"));
      render();
      fileInput.click();
    });
    ["dragenter", "dragover"].forEach(t => zone.addEventListener(t, e => { e.preventDefault(); zone.classList.add("is-drag"); }));
    ["dragleave", "drop"].forEach(t => zone.addEventListener(t, e => { e.preventDefault(); zone.classList.remove("is-drag"); }));
    zone.addEventListener("drop", e => {
      if (!e.dataTransfer.files.length) return;
      fileInput.files = e.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change"));
    });

    submit.addEventListener("click", async () => {
      const week = selectedWeek();
      if (week && week.dataset.uploaded === "true" && !replacing) {
        window.location.href = upload.dataset.continueUrl;
        return;
      }
      submit.disabled = true;
      submit.textContent = "Uploading…";
      showError("");
      try {
        const res = await fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || "Upload failed — please try again.");
        window.location.href = data.next;
      } catch (err) {
        showError(err.message);
        render();
      }
    });
    render();
  }

  // ── Preferences flow ────────────────────────────────────────────────────
  const prefs = $("[data-prefs]");
  if (prefs) {
    const questionsView = $("[data-questions]", prefs);
    const reviewView = $("[data-review]", prefs);
    const steps = $$("[data-step]", prefs);
    let index = 0;
    let autoTimer = null;

    const excluded = () => new Set($$("input[name=hard_excludes]:checked", prefs).map(i => i.value));
    function questionHidden(q) {
      const when = (q.dataset.hideWhen || "").split(",").filter(Boolean);
      const ex = excluded();
      return when.some(v => ex.has(v));
    }
    function applyVisibility() {
      $$("[data-q]", prefs).forEach(q => {
        const hide = questionHidden(q);
        q.hidden = hide;
        // Hidden questions aren't submitted, so a saved answer is kept.
        $$("input, select", q).forEach(i => { i.disabled = hide; });
      });
    }
    const visibleSteps = () => steps.filter(s => $$("[data-q]", s).some(q => !questionHidden(q)));

    function show(i) {
      clearTimeout(autoTimer);
      const vis = visibleSteps();
      index = Math.max(0, Math.min(i, vis.length - 1));
      const current = vis[index];
      steps.forEach(s => { s.hidden = s !== current; });
      questionsView.hidden = false;
      reviewView.hidden = true;
      $("[data-step-count]", prefs).textContent = `Step ${index + 1} of ${vis.length}`;
      $("[data-step-bar]", prefs).style.width = `${((index + 1) / vis.length) * 100}%`;
      $("[data-intro]", prefs).hidden = index !== 0;
      const cat = current.dataset.cat;
      $("[data-optional-note]", prefs).hidden = cat !== "4";
      $$(".cat-chip", prefs).forEach(c => c.classList.toggle("is-current", c.dataset.jumpCat === cat));
      $("[data-prefs-next]", prefs).textContent = index < vis.length - 1 ? "Next" : "Review answers";
      window.scrollTo(0, 0);
    }
    function next() {
      const vis = visibleSteps();
      if (index < vis.length - 1) show(index + 1); else showReview();
    }

    function answerText(q) {
      const text = $("input[type=text]", q);
      if (text) return text.value.trim();
      return $$("input:checked", q).map(i => i.parentElement.textContent.trim()).join(", ");
    }
    function showReview() {
      $$("[data-review-cat]", prefs).forEach(card => {
        card.innerHTML = "";
        steps.filter(s => s.dataset.cat === card.dataset.reviewCat).forEach(s => {
          $$("[data-q]", s).filter(q => !questionHidden(q)).forEach(q => {
            const row = document.createElement("div");
            row.className = "kv";
            const k = document.createElement("span");
            k.className = "kv-k";
            k.textContent = q.dataset.prompt;
            const v = document.createElement("span");
            const value = answerText(q);
            v.className = "kv-v" + (value ? "" : " is-empty");
            v.textContent = value || "Skipped";
            row.append(k, v);
            card.appendChild(row);
          });
        });
      });
      questionsView.hidden = true;
      reviewView.hidden = false;
      window.scrollTo(0, 0);
    }

    prefs.addEventListener("change", e => {
      if (e.target.name === "hard_excludes") applyVisibility();
      // A step that's just one single-choice question moves on by itself.
      const step = e.target.closest("[data-step]");
      if (step && e.target.type === "radio") {
        const visibleQs = $$("[data-q]", step).filter(q => !questionHidden(q));
        if (visibleQs.length === 1 && visibleQs[0].dataset.kind === "single") {
          clearTimeout(autoTimer);
          autoTimer = setTimeout(next, 700);
        }
      }
    });
    // Enter in a text box shouldn't submit the whole form mid-flow.
    prefs.addEventListener("keydown", e => {
      if (e.key === "Enter" && e.target.matches("input[type=text]") && reviewView.hidden) { e.preventDefault(); next(); }
    });
    $("[data-prefs-next]", prefs).addEventListener("click", next);
    $("[data-prefs-skip]", prefs).addEventListener("click", next);
    $("[data-prefs-back]", prefs).addEventListener("click", () => {
      if (index > 0) show(index - 1); else window.location.href = prefs.dataset.backUrl;
    });
    $("[data-review-back]", prefs).addEventListener("click", () => show(visibleSteps().length - 1));
    $$("[data-jump-cat]", prefs).forEach(btn => btn.addEventListener("click", () => {
      const target = visibleSteps().findIndex(s => s.dataset.cat === btn.dataset.jumpCat);
      if (target !== -1) show(target);
    }));

    applyVisibility();
    show(0);
  }

  // ── Check your menu: wait for Gemini ────────────────────────────────────
  const check = $("[data-check]");
  if (check && check.dataset.status === "processing") {
    const poll = async () => {
      try {
        const res = await fetch(check.dataset.statusUrl, { credentials: "same-origin" });
        const data = await res.json();
        if (data.status !== "processing") { window.location.reload(); return; }
      } catch (err) { /* transient — try again */ }
      setTimeout(poll, 2500);
    };
    setTimeout(poll, 2000);
  }
  const confirmForm = $("[data-confirm-form]");
  if (confirmForm) {
    confirmForm.addEventListener("submit", () => {
      const btn = $("button[type=submit]", confirmForm);
      btn.disabled = true;
      btn.textContent = "Confirming…";
    });
  }

  // ── Almost there: real scheduling progress ──────────────────────────────
  const sched = $("[data-scheduling]");
  if (sched) {
    const started = Date.now();
    const message = $("[data-sched-message]", sched);
    const count = $("[data-sched-count]", sched);
    const poll = async () => {
      let data = null;
      try {
        const res = await fetch("/menu/scheduling/status", { credentials: "same-origin" });
        data = await res.json();
      } catch (err) { /* transient — try again */ }
      if (data) {
        if (data.error === "needs_reauth") {
          $("[data-sched-running]", sched).hidden = true;
          $("[data-sched-reauth]", sched).hidden = false;
          return;
        }
        if (data.finished) { window.location.href = sched.dataset.nextUrl; return; }
        if (data.total > 0) {
          if (message.textContent !== "Adding meals to your Google Calendar…") {
            message.textContent = "Adding meals to your Google Calendar…";
            message.classList.remove("fade-in"); void message.offsetWidth; message.classList.add("fade-in");
          }
          count.hidden = false;
          count.textContent = `Adding meals… ${data.done} of ${data.total}`;
        }
      }
      if (Date.now() - started > 120000) {
        $("[data-sched-running]", sched).hidden = true;
        $("[data-sched-slow]", sched).hidden = false;
      }
      setTimeout(poll, 1500);
    };
    setTimeout(poll, 800);
  }

  // ── This week: rating sheet ─────────────────────────────────────────────
  const sheet = $("[data-rating-sheet]");
  if (sheet) {
    const saveBtn = $("[data-save-ratings]", sheet);
    saveBtn.addEventListener("click", async () => {
      if (!ratedCards(sheet).length) { closeDialog(sheet); return; }
      saveBtn.disabled = true;
      try {
        await saveRatings(sheet);
        toast("Feedback saved");
        setTimeout(() => window.location.reload(), 900);
      } catch (err) {
        saveBtn.disabled = false;
        toast("Couldn't save — please try again");
      }
    });
  }

  // ── Weekly check-in steps ───────────────────────────────────────────────
  const checkin = $("[data-checkin]");
  if (checkin) {
    const steps = $$("[data-step]", checkin);
    const counted = steps.filter(s => s.dataset.kind !== "done");
    let index = 0;
    let autoTimer = null;
    function show(i) {
      clearTimeout(autoTimer);
      index = Math.max(0, Math.min(i, steps.length - 1));
      steps.forEach((s, n) => { s.hidden = n !== index; });
      const done = steps[index].dataset.kind === "done";
      const bar = $("[data-step-bar]");
      const label = $("[data-step-count]");
      if (bar) bar.parentElement.hidden = done;
      if (label) {
        label.hidden = done;
        label.textContent = `Step ${Math.min(index + 1, counted.length)} of ${counted.length}`;
      }
      if (bar) bar.style.width = `${(Math.min(index + 1, counted.length) / counted.length) * 100}%`;
      window.scrollTo(0, 0);
    }
    const next = () => show(index + 1);
    checkin.addEventListener("change", e => {
      const step = e.target.closest("[data-step]");
      if (!step) return;
      const btn = $("[data-next]", step);
      if (btn && step.dataset.kind === "multi") btn.textContent = $$("input:checked", step).length ? (btn.hasAttribute("data-final") ? "Done" : "Next") : "Skip";
      if (step.dataset.kind === "single" && e.target.type === "radio") autoTimer = setTimeout(next, 500);
    });
    checkin.addEventListener("click", async e => {
      if (e.target.closest("[data-next]")) { next(); return; }
      const save = e.target.closest("[data-save-ratings][data-then-next]");
      if (!save) return;
      save.disabled = true;
      try {
        if (await saveRatings(save.closest("[data-step]"))) toast("Feedback saved");
        next();
      } catch (err) {
        toast("Couldn't save — please try again");
      } finally {
        save.disabled = false;
      }
    });
    $("[data-checkin-back]").addEventListener("click", () => {
      if (index > 0) show(index - 1); else window.location.href = "/";
    });
    show(0);
  }

  // ── My tastes: Like / Neutral / Avoid edits ─────────────────────────────
  const tastes = $("[data-tastes-form]");
  if (tastes) {
    const save = $("[data-tastes-save]", tastes);
    tastes.addEventListener("change", e => {
      if (!e.target.matches("[data-choice]")) return;
      const row = e.target.closest(".learned-row");
      $("[data-choice-out]", row).value = e.target.value;
      save.disabled = !$$(".learned-row", tastes).some(r => $("[data-choice-out]", r).value !== $("input[name=current]", r).value);
    });
    // Send only the rows that changed — a long learned history would
    // otherwise post thousands of fields for a one-dish edit.
    tastes.addEventListener("submit", () => {
      $$("[data-choice]", tastes).forEach(i => { i.disabled = true; });
      $$(".learned-row", tastes).forEach(r => {
        if ($("[data-choice-out]", r).value === $("input[name=current]", r).value) {
          $$("input", r).forEach(i => { i.disabled = true; });
        }
      });
    });
    $$("[data-show-all]", tastes).forEach(btn => btn.addEventListener("click", () => {
      $$("[data-extra]", btn.closest("[data-group]")).forEach(r => { r.hidden = false; });
      btn.remove();
    }));
  }

  // ── Settings: AI key Save enables once a key is typed ──────────────────
  const keyInput = $("[data-key-input]");
  if (keyInput) {
    const save = $("[data-key-save]");
    keyInput.addEventListener("input", () => { save.disabled = keyInput.value.trim().length < 8; });
  }
})();
