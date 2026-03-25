const SITE_LINKS = {
  repo: "https://github.com/arno-create/Bdayblaze",
  invite: "",
  support: "",
};

const OPTIONAL_KEYS = new Set(["invite", "support"]);

for (const node of document.querySelectorAll("[data-site-link]")) {
  const key = node.getAttribute("data-site-link");
  if (!key) {
    continue;
  }
  const href = SITE_LINKS[key] ?? "";
  if (!href) {
    if (OPTIONAL_KEYS.has(key)) {
      node.hidden = true;
    }
    continue;
  }
  node.setAttribute("href", href);
  node.setAttribute("target", "_blank");
  node.setAttribute("rel", "noreferrer");
}

const yearNode = document.getElementById("site-year");
if (yearNode) {
  yearNode.textContent = String(new Date().getFullYear());
}
