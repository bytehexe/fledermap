// src/fledermap/web/static/classifier_box.js -- the recording-details page's manual
// classification tag editor (design spec 2026-09-05-fledermap-manual-classification-
// design.md, §5). No frontend build step in this project -- vanilla JS, matching
// audio_controls.js/recording_detail.js's own style.
//
// `initClassifierBox` is a function (not a one-shot DOMContentLoaded closure) because
// every save() swaps the box's outerHTML for the server's freshly-rendered fragment --
// replacing outerHTML does NOT re-fire DOMContentLoaded or re-attach listeners to the
// new DOM nodes, so each swapped-in box must be re-initialized explicitly.
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("classifier-box");
  if (!box) return;
  initClassifierBox(box);
});

function initClassifierBox(box) {
  const searchIndex = JSON.parse(box.dataset.taxonSearchIndex);
  const audioHash = box.dataset.audioHash;
  const tagsEl = box.querySelector("#classifier-tags");
  const searchEl = box.querySelector("#classifier-search");
  const suggestionsEl = box.querySelector("#classifier-suggestions");
  const clearButton = box.querySelector("#classifier-clear");

  function currentTaxonIds() {
    return Array.from(tagsEl.querySelectorAll(".classifier-chip")).map((el) =>
      el.dataset.taxonId,
    );
  }

  function renderSuggestions(query) {
    suggestionsEl.innerHTML = "";
    if (!query) {
      suggestionsEl.hidden = true;
      return;
    }
    const already = new Set(currentTaxonIds().map(String));
    const matches = searchIndex
      .filter((t) => !already.has(String(t.id)) && matchesQuery(t, query))
      .slice(0, 10);
    if (matches.length === 0) {
      // Nothing matched -- rather than just hiding the dropdown and leaving
      // the user stuck, point at the frequency-class fallbacks (HiF/LoF/
      // Hilo, always present in searchIndex) they may not know exist.
      const hint = document.createElement("li");
      hint.className = "classifier-suggestion-hint";
      hint.textContent = 'No match. Try "HiF", "LoF", or "Hilo" for a frequency-class group.';
      suggestionsEl.appendChild(hint);
      suggestionsEl.hidden = false;
      return;
    }
    for (const taxon of matches) {
      const li = document.createElement("li");
      li.textContent = taxon.scientific_name;
      li.dataset.taxonId = taxon.id;
      li.addEventListener("click", () => {
        addChip(taxon);
        searchEl.value = "";
        suggestionsEl.hidden = true;
      });
      suggestionsEl.appendChild(li);
    }
    suggestionsEl.hidden = false;
  }

  function addChip(taxon) {
    // Adding a species/group chip always means "classify as species" --
    // clears any active No ID/Noise selection first (mutually exclusive,
    // per the design's within-MANUAL exclusivity rule; the server enforces
    // this for real, this is just immediate UI feedback).
    box
      .querySelectorAll(".classifier-verdict-button[aria-pressed='true']")
      .forEach((btn) => btn.setAttribute("aria-pressed", "false"));
    searchEl.disabled = false;

    const chip = document.createElement("span");
    chip.className = "classifier-chip";
    chip.dataset.taxonId = taxon.id;
    chip.textContent = taxon.scientific_name + " ";
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "classifier-chip-remove";
    removeButton.setAttribute("aria-label", "Remove " + taxon.scientific_name);
    removeButton.textContent = "×";
    removeButton.addEventListener("click", () => {
      chip.remove();
      save();
    });
    chip.appendChild(removeButton);
    tagsEl.appendChild(chip);
    save();
  }

  function save() {
    const activeVerdictButton = box.querySelector(
      ".classifier-verdict-button[aria-pressed='true']",
    );
    const payload = buildSaveBody({
      activeVerdict: activeVerdictButton ? activeVerdictButton.dataset.verdict : null,
      taxonIds: currentTaxonIds(),
    });
    const body = new URLSearchParams();
    if (payload.verdict) body.append("verdict", payload.verdict);
    if (payload.taxonIds) {
      for (const id of payload.taxonIds) body.append("taxon_ids", id);
    }

    fetch(`/recordings/${audioHash}/manual-classification`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    })
      .then((response) => {
        // A 400/404 response is plain text, not the classifier-box HTML
        // fragment -- must not be swapped in as if it were (that would
        // replace the whole box with the literal error text and then throw
        // on the next initClassifierBox(null)). Surface it instead. NOTE:
        // this does NOT leave the box as it was before this save -- the
        // optimistic DOM mutation (chip removed, aria-pressed toggled,
        // etc.) already happened before fetch() was called and is never
        // reverted here, so a failed save leaves the user looking at a
        // state the server rejected until they reload the page.
        if (!response.ok) {
          return response.text().then((text) => {
            throw new Error(text || `Request failed (${response.status})`);
          });
        }
        return response.text();
      })
      .then((html) => {
        const wrapper = document.createElement("div");
        wrapper.innerHTML = html;
        const newBox = wrapper.firstElementChild;
        box.replaceWith(newBox);
        initClassifierBox(newBox);
      })
      .catch((error) => {
        // Keep it simple -- this project has no toast/notification system
        // (CLAUDE.md/app.css survey confirms none exists yet), and a failed
        // save is rare enough that a blocking alert is an acceptable, if
        // blunt, way to make sure it isn't missed silently.
        alert("Could not save classification: " + error.message);
      });
  }

  // Server-rendered chips (present at page load, or surviving a save's
  // re-rendered box) have no listener of their own yet -- addChip only wires
  // up the ephemeral chip IT creates client-side, which is destroyed the
  // instant a save swaps in the server's fragment. Without this, removing
  // any chip that wasn't added in the current in-memory session (i.e. every
  // chip on first load, and every chip after the very first save) silently
  // does nothing.
  box.querySelectorAll(".classifier-chip-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.closest(".classifier-chip").remove();
      save();
    });
  });

  searchEl.addEventListener("input", () => renderSuggestions(searchEl.value));

  box.querySelectorAll(".classifier-verdict-button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const alreadyActive = btn.getAttribute("aria-pressed") === "true";
      box
        .querySelectorAll(".classifier-verdict-button")
        .forEach((b) => b.setAttribute("aria-pressed", "false"));
      if (!alreadyActive) {
        btn.setAttribute("aria-pressed", "true");
        tagsEl.innerHTML = "";
        searchEl.disabled = true;
      } else {
        searchEl.disabled = false;
      }
      save();
    });
  });

  clearButton.addEventListener("click", () => {
    tagsEl.innerHTML = "";
    box
      .querySelectorAll(".classifier-verdict-button[aria-pressed='true']")
      .forEach((btn) => btn.setAttribute("aria-pressed", "false"));
    searchEl.disabled = false;
    save();
  });
}
