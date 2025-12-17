document.querySelectorAll("[data-toggle]").forEach((h) => {
  h.addEventListener("click", (e) => {
    if (e.target.closest("button")) return; // don't toggle when clicking buttons
    const section = h.closest(".section");
    section.classList.toggle("open");
  });
});
