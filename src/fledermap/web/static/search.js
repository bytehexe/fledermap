document.addEventListener("DOMContentLoaded", () => {
  const helpBtn = document.getElementById("search-help-btn");
  const modal = document.getElementById("search-help-modal");
  const backdrop = document.getElementById("search-help-backdrop");
  const closeBtn = document.getElementById("search-help-close");
  if (!helpBtn || !modal || !backdrop || !closeBtn) return;

  function closeHelp() {
    modal.hidden = true;
    backdrop.hidden = true;
    document.removeEventListener("keydown", onKeydown);
  }

  function onKeydown(event) {
    if (event.key === "Escape") closeHelp();
  }

  function openHelp() {
    modal.hidden = false;
    backdrop.hidden = false;
    document.addEventListener("keydown", onKeydown);
  }

  helpBtn.addEventListener("click", openHelp);
  closeBtn.addEventListener("click", closeHelp);
  // Unlike the comment popup's backdrop (recording_detail.js), this modal holds no
  // in-progress draft that a stray click could discard -- backdrop-click-to-dismiss is safe here.
  backdrop.addEventListener("click", closeHelp);
});

if (typeof module !== "undefined" && module.exports) {
  module.exports = {};
}
