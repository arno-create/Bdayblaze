(() => {
  document.documentElement.classList.add("js-ready");

  const year = String(new Date().getFullYear());
  for (const node of document.querySelectorAll("[data-year]")) {
    node.textContent = year;
  }

  const mobileHeaderBoundary = window.matchMedia("(max-width: 840px)");

  const closeHeaderMenu = (toggle, menu) => {
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "Open site menu");
    menu.classList.remove("is-open");
  };

  const openHeaderMenu = (toggle, menu) => {
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-label", "Close site menu");
    menu.classList.add("is-open");
  };

  for (const header of document.querySelectorAll(".site-header")) {
    const toggle = header.querySelector(".menu-toggle");
    const menu = header.querySelector(".header-menu");

    if (!(toggle instanceof HTMLButtonElement) || !(menu instanceof HTMLElement)) {
      continue;
    }

    closeHeaderMenu(toggle, menu);

    toggle.addEventListener("click", () => {
      const isOpen = toggle.getAttribute("aria-expanded") === "true";
      if (isOpen) {
        closeHeaderMenu(toggle, menu);
        return;
      }

      openHeaderMenu(toggle, menu);
    });

    header.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }

      if (target.closest(".site-nav a, .header-actions a")) {
        closeHeaderMenu(toggle, menu);
      }
    });

    document.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }

      if (!header.contains(target)) {
        closeHeaderMenu(toggle, menu);
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") {
        return;
      }

      if (toggle.getAttribute("aria-expanded") !== "true") {
        return;
      }

      closeHeaderMenu(toggle, menu);
      toggle.focus();
    });
  }

  mobileHeaderBoundary.addEventListener("change", (event) => {
    if (event.matches) {
      return;
    }

    for (const header of document.querySelectorAll(".site-header")) {
      const toggle = header.querySelector(".menu-toggle");
      const menu = header.querySelector(".header-menu");

      if (!(toggle instanceof HTMLButtonElement) || !(menu instanceof HTMLElement)) {
        continue;
      }

      closeHeaderMenu(toggle, menu);
    }
  });

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

  const heroMedia = document.querySelector(".hero-media");
  if (!(heroMedia instanceof HTMLElement)) {
    return;
  }

  const resetPointer = () => {
    heroMedia.style.setProperty("--pointer-x", "0");
    heroMedia.style.setProperty("--pointer-y", "0");
  };

  heroMedia.addEventListener("pointermove", (event) => {
    const rect = heroMedia.getBoundingClientRect();
    const normalizedX = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    const normalizedY = ((event.clientY - rect.top) / rect.height) * 2 - 1;

    heroMedia.style.setProperty("--pointer-x", normalizedX.toFixed(3));
    heroMedia.style.setProperty("--pointer-y", normalizedY.toFixed(3));
  });

  heroMedia.addEventListener("pointerleave", resetPointer);
  resetPointer();
})();
