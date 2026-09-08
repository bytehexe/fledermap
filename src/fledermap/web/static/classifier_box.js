// src/fledermap/web/static/classifier_box.js -- the recording-details page's manual
// classification tag editor (design spec 2026-09-05-fledermap-manual-classification-
// design.md, §5). No frontend build step in this project -- vanilla JS, matching
// audio_controls.js/recording_detail.js's own style.
//
// Chip add/remove is a purely client-side edit of the staged set -- nothing reaches the
// server until Save is pressed (docs/style-guide.md's "a form that changes stored data
// needs an explicit Save" rule). `classifierBoxDirty` tracks whether there are unsaved
// edits; a single beforeunload listener (registered once, below, not per box-init) warns
// before leaving the page while it's true.
//
// `initClassifierBox` is a function (not a one-shot DOMContentLoaded closure) because
// every successful save() swaps the box's outerHTML for the server's freshly-rendered
// fragment -- replacing outerHTML does NOT re-fire DOMContentLoaded or re-attach
// listeners to the new DOM nodes, so each swapped-in box must be re-initialized
// explicitly.
let classifierBoxDirty = false;

document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("classifier-box");
  if (!box) return;
  initClassifierBox(box);

  window.addEventListener("beforeunload", (event) => {
    if (!classifierBoxDirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
});

function initClassifierBox(box) {
  // No ID/Noise are just two more searchable entries in the same list real
  // taxa come from (classifier_logic.js's SENTINEL_ENTRIES) -- see
  // _classifier_box.html's comment for why.
  const searchIndex = [...SENTINEL_ENTRIES, ...JSON.parse(box.dataset.taxonSearchIndex)];
  const audioHash = box.dataset.audioHash;
  const tagsEl = box.querySelector("#classifier-tags");
  const searchEl = box.querySelector("#classifier-search");
  const suggestionsEl = box.querySelector("#classifier-suggestions");
  const errorEl = box.querySelector("#classifier-error");
  const saveButton = box.querySelector("#classifier-save");

  function entryKey(entry) {
    return entry.sentinelVerdict ? `sentinel:${entry.sentinelVerdict}` : `taxon:${entry.id}`;
  }

  function chipKey(chipEl) {
    return chipEl.dataset.sentinelVerdict
      ? `sentinel:${chipEl.dataset.sentinelVerdict}`
      : `taxon:${chipEl.dataset.taxonId}`;
  }

  function currentChips() {
    return Array.from(tagsEl.querySelectorAll(".classifier-chip")).map((el) =>
      el.dataset.sentinelVerdict
        ? { kind: "sentinel", verdict: el.dataset.sentinelVerdict }
        : { kind: "taxon", id: el.dataset.taxonId },
    );
  }

  function wireRemoveButton(chipEl) {
    chipEl.querySelector(".classifier-chip-remove").addEventListener("click", () => {
      chipEl.remove();
      classifierBoxDirty = true;
    });
  }

  function renderSuggestions(query) {
    suggestionsEl.innerHTML = "";
    if (!query) {
      suggestionsEl.hidden = true;
      return;
    }
    const already = new Set(
      Array.from(tagsEl.querySelectorAll(".classifier-chip")).map(chipKey),
    );
    const matches = searchIndex
      .filter((entry) => !already.has(entryKey(entry)) && matchesQuery(entry, query))
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
    for (const entry of matches) {
      const li = document.createElement("li");
      li.textContent = entry.scientific_name;
      li.addEventListener("click", () => {
        addChip(entry);
        searchEl.value = "";
        suggestionsEl.hidden = true;
      });
      suggestionsEl.appendChild(li);
    }
    suggestionsEl.hidden = false;
  }

  function addChip(entry) {
    const chip = document.createElement("span");
    chip.className = "classifier-chip";
    if (entry.sentinelVerdict) {
      chip.dataset.sentinelVerdict = entry.sentinelVerdict;
    } else {
      chip.dataset.taxonId = entry.id;
    }
    chip.textContent = entry.scientific_name + " ";
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "classifier-chip-remove";
    removeButton.setAttribute("aria-label", "Remove " + entry.scientific_name);
    removeButton.textContent = "×";
    chip.appendChild(removeButton);
    tagsEl.appendChild(chip);
    wireRemoveButton(chip);
    classifierBoxDirty = true;
  }

  function save() {
    const result = deriveSavePayload(currentChips());
    if (!result.ok) {
      // A staged combination that can't collapse to one valid state (e.g.
      // a species chip alongside No ID) -- caught here, at Save, rather
      // than prevented while editing (docs/style-guide.md's rule doesn't
      // require live prevention, and the whole point of chips over toggle
      // buttons was to stop disabling controls out from under the user).
      errorEl.textContent = result.error;
      errorEl.hidden = false;
      return;
    }
    errorEl.hidden = true;

    const body = new URLSearchParams();
    if (result.verdict) body.append("verdict", result.verdict);
    for (const id of result.taxonIds) body.append("taxon_ids", id);

    fetch(`/recordings/${audioHash}/manual-classification`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    })
      .then((response) => {
        // A 400/404 response is plain text, not the classifier-box HTML
        // fragment -- must not be swapped in as if it were.
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
        classifierBoxDirty = false;
        initClassifierBox(newBox);
      })
      .catch((error) => {
        errorEl.textContent = "Could not save: " + error.message;
        errorEl.hidden = false;
      });
  }

  // Server-rendered chips (present at page load, or surviving a save's
  // re-rendered box) have no listener of their own yet.
  tagsEl.querySelectorAll(".classifier-chip").forEach(wireRemoveButton);

  searchEl.addEventListener("input", () => renderSuggestions(searchEl.value));
  saveButton.addEventListener("click", save);
}
