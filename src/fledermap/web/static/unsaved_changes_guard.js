// src/fledermap/web/static/unsaved_changes_guard.js -- style guide's "A page holding
// unsaved changes must warn before it's left unsaved" rule, for a real HTML <form>
// (a plain POST + full-page reload/redirect), as opposed to classifier_box.js's own
// beforeunload guard, which tracks an AJAX save flow's dirty flag itself. Written as a
// small reusable helper rather than a second bespoke copy, per the style guide's
// "promote on second use" rule -- session_detail.html's session-edit form and its
// merge-resolution form are the first two callers.
//
// A real form submission IS a navigation, so without clearing the dirty flag on the
// form's own `submit` event first, clicking the form's own Save/Accept/Reject button
// would trigger the exact same "leave without saving?" browser dialog this exists to
// prevent for everything else -- the same reasoning the style guide gives for why a
// Cancel button clears the dirty flag before it navigates.
function guardUnsavedChanges(form) {
  if (!form) return;
  let dirty = false;
  const snapshot = new FormData(form);

  function currentlyDirty() {
    const current = new FormData(form);
    for (const [key, value] of current.entries()) {
      if (snapshot.get(key) !== value) return true;
    }
    return false;
  }

  form.addEventListener("input", () => {
    dirty = currentlyDirty();
  });
  form.addEventListener("change", () => {
    dirty = currentlyDirty();
  });
  form.addEventListener("submit", () => {
    dirty = false;
  });

  window.addEventListener("beforeunload", (event) => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

// Wired directly here (not split into a separate DOM-touching consumer file, unlike
// marker_colors.js/classifier_logic.js) -- `guardUnsavedChanges` above isn't pure DOM-
// free logic to begin with (it touches `form`/`FormData`/`window` throughout), so
// there's no node:test-coverable half to extract; this whole file lives in the same
// "DOM-touching, live-verify only" bucket as session_map.js and classifier_box.js's own
// beforeunload guard, which are likewise never `require()`-d.
document.addEventListener("DOMContentLoaded", () => {
  guardUnsavedChanges(document.getElementById("session-edit-form"));
  guardUnsavedChanges(document.getElementById("merge-resolution-form"));
});
