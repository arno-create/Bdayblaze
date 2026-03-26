(() => {
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
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    const revealableNodes = [];
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) {
            continue;
          }
          entry.target.classList.remove("reveal-ready");
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
      const rect = node.getBoundingClientRect();
      const belowFold = rect.top > viewportHeight * 0.85;
      if (!belowFold) {
        node.classList.add("is-visible");
        return;
      }

      node.classList.add("reveal-ready");
      node.style.setProperty("--reveal-delay", `${Math.min(index * 70, 280)}ms`);
      revealableNodes.push(node);
      observer.observe(node);
    });

    window.setTimeout(() => {
      for (const node of revealableNodes) {
        if (node.classList.contains("is-visible")) {
          continue;
        }
        node.classList.remove("reveal-ready");
        node.classList.add("is-visible");
      }
    }, 1600);
  } else {
    for (const node of revealNodes) {
      node.classList.add("is-visible");
    }
  }

  const coarsePointer = window.matchMedia("(pointer: coarse)").matches;

  if (prefersReducedMotion || coarsePointer) {
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
