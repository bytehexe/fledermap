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

  function matchesQuery(taxon, query) {
    const q = query.toLowerCase();
    const fields = [
      taxon.scientific_name,
      taxon.common_name_en,
      taxon.common_name_de,
      ...(taxon.codes || []),
    ];
    return fields.some((f) => f && f.toLowerCase().includes(q));
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
      suggestionsEl.hidden = true;
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
    const body = new URLSearchParams();
    const activeVerdictButton = box.querySelector(
      ".classifier-verdict-button[aria-pressed='true']",
    );
    if (activeVerdictButton) {
      body.append("verdict", activeVerdictButton.dataset.verdict);
    } else if (currentTaxonIds().length > 0) {
      body.append("verdict", "species");
      for (const id of currentTaxonIds()) body.append("taxon_ids", id);
    }
    // Neither branch: "clear" -- verdict omitted entirely, matching
    // set_manual_classification's verdict=None contract.

    fetch(`/recordings/${audioHash}/manual-classification`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    })
      .then((response) => response.text())
      .then((html) => {
        const wrapper = document.createElement("div");
        wrapper.innerHTML = html;
        const newBox = wrapper.firstElementChild;
        box.replaceWith(newBox);
        initClassifierBox(newBox);
      });
  }

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
