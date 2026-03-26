(() => {
  document.documentElement.classList.add("js");

  const year = String(new Date().getFullYear());
  for (const node of document.querySelectorAll("[data-year]")) {
    node.textContent = year;
  }

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const revealNodes = Array.from(document.querySelectorAll("[data-reveal]"));

  if (prefersReducedMotion) {
    for (const node of revealNodes) {
      node.classList.add("is-visible");
    }
  } else if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) {
            continue;
          }
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      },
      {
        threshold: 0.18,
        rootMargin: "0px 0px -8% 0px",
      },
    );

    revealNodes.forEach((node, index) => {
      node.style.setProperty("--reveal-delay", `${Math.min(index * 70, 280)}ms`);
      observer.observe(node);
    });
  } else {
    for (const node of revealNodes) {
      node.classList.add("is-visible");
    }
  }

  if (prefersReducedMotion) {
    return;
  }

  const heroStage = document.querySelector(".hero-stage");
  if (!(heroStage instanceof HTMLElement)) {
    return;
  }

  const resetPointer = () => {
    heroStage.style.setProperty("--pointer-x", "0");
    heroStage.style.setProperty("--pointer-y", "0");
  };

  heroStage.addEventListener("pointermove", (event) => {
    const rect = heroStage.getBoundingClientRect();
    const normalizedX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const normalizedY = ((event.clientY - rect.top) / rect.height) * 2 - 1;

    heroStage.style.setProperty("--pointer-x", normalizedX.toFixed(3));
    heroStage.style.setProperty("--pointer-y", normalizedY.toFixed(3));
  });

  heroStage.addEventListener("pointerleave", resetPointer);
  resetPointer();
})();
