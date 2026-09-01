(function () {
  "use strict";

  var FACETS = [
    null, "ACCUR", "INTEG", "QUALI", "TRUST", "PROGR",
    "SPEED", "COST", "SECC", "SECI", "SECA", "SECP"
  ];
  var TIERS = [null, 10, 20, 30, 40];
  var ENFORCEMENT = [
    "prose-only", "hook-linked", "gate-linked"
  ];
  var SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
  var CORPUS_ID = /^[a-z0-9]{6,}$/;
  var DIGEST = /^[0-9a-f]{64}$/;

  function exactKeys(value, expected, where) {
    if (!value ||
        Object.prototype.toString.call(value) !== "[object Object]") {
      throw new Error(where + " must be an object");
    }
    var actual = Object.keys(value).sort();
    var wanted = expected.slice().sort();
    if (actual.length !== wanted.length ||
        actual.some(function (key, index) {
          return key !== wanted[index];
        })) {
      throw new Error(where + " has an unexpected shape");
    }
  }

  function nonEmptyString(value) {
    return typeof value === "string" &&
      value.trim().length > 0;
  }

  function uniqueStrings(values) {
    return Array.isArray(values) &&
      values.length > 0 &&
      values.every(nonEmptyString) &&
      new Set(values).size === values.length;
  }

  function validateRulesetShape(data) {
    exactKeys(
      data,
      [
        "schema", "model-sha256", "conditions",
        "profiles", "rules"
      ],
      "ruleset"
    );
    if (data.schema !== 1 ||
        !DIGEST.test(data["model-sha256"])) {
      throw new Error(
        "unsupported ruleset schema or fingerprint"
      );
    }
    if (!Array.isArray(data.conditions) ||
        data.conditions.length === 0 ||
        !Array.isArray(data.profiles) ||
        data.profiles.length === 0 ||
        !Array.isArray(data.rules) ||
        data.rules.length === 0) {
      throw new Error("ruleset arrays must be non-empty");
    }

    var conditionSlugs = new Set();
    data.conditions.forEach(function (row, index) {
      exactKeys(
        row,
        ["slug", "question", "description"],
        "condition " + index
      );
      if (!SLUG.test(row.slug) ||
          !nonEmptyString(row.question) ||
          !nonEmptyString(row.description) ||
          conditionSlugs.has(row.slug)) {
        throw new Error("invalid or duplicate condition");
      }
      conditionSlugs.add(row.slug);
    });
    if (!conditionSlugs.has("always")) {
      throw new Error("the always condition is required");
    }

    var profileSlugs = new Set();
    var fullCorpus = null;
    data.profiles.forEach(function (profile, index) {
      exactKeys(
        profile,
        ["name", "slug", "conditions"],
        "profile " + index
      );
      if (!nonEmptyString(profile.name) ||
          !SLUG.test(profile.slug) ||
          profileSlugs.has(profile.slug) ||
          !uniqueStrings(profile.conditions) ||
          !profile.conditions.every(function (slug) {
            return conditionSlugs.has(slug);
          }) ||
          profile.conditions.indexOf("always") === -1) {
        throw new Error("invalid or duplicate profile");
      }
      profileSlugs.add(profile.slug);
      if (profile.slug === "full-corpus") {
        fullCorpus = profile;
      }
    });
    if (!fullCorpus ||
        fullCorpus.conditions.length !== conditionSlugs.size ||
        !fullCorpus.conditions.every(function (slug) {
          return conditionSlugs.has(slug);
        })) {
      throw new Error(
        "full-corpus must contain every condition"
      );
    }

    var ruleIds = new Set();
    data.rules.forEach(function (rule, index) {
      exactKeys(
        rule,
        [
          "corpus-id", "title", "applies", "facet", "tier",
          "family", "enforcement"
        ],
        "rule " + index
      );
      if (!CORPUS_ID.test(rule["corpus-id"]) ||
          ruleIds.has(rule["corpus-id"]) ||
          !nonEmptyString(rule.title) ||
          !uniqueStrings(rule.applies) ||
          !rule.applies.every(function (slug) {
            return conditionSlugs.has(slug);
          }) ||
          (rule.applies.indexOf("always") !== -1 &&
           rule.applies.length !== 1) ||
          FACETS.indexOf(rule.facet) === -1 ||
          TIERS.indexOf(rule.tier) === -1 ||
          ["aiqt", "security"].indexOf(rule.family) === -1 ||
          ENFORCEMENT.indexOf(rule.enforcement) === -1) {
        throw new Error("invalid or duplicate rule");
      }
      ruleIds.add(rule["corpus-id"]);
    });
  }

  function effectiveRuleIds(data, selected) {
    var ids = new Set();
    data.rules.forEach(function (rule) {
      if (rule.applies.some(function (condition) {
        return selected.has(condition);
      })) {
        ids.add(rule["corpus-id"]);
      }
    });
    return ids;
  }

  function profileState(profile, selected) {
    var nonFloor = profile.conditions.filter(
      function (condition) {
        return condition !== "always";
      }
    );
    var all = profile.conditions.every(
      function (condition) {
        return selected.has(condition);
      }
    );
    var some = nonFloor.some(function (condition) {
      return selected.has(condition);
    });
    return all ? "active" : (some ? "partial" : "inactive");
  }

  function toggleProfile(profile, data, selected) {
    if (profileState(profile, selected) !== "active") {
      profile.conditions.forEach(function (condition) {
        selected.add(condition);
      });
      selected.add("always");
      return;
    }

    var otherActive = data.profiles.filter(
      function (candidate) {
        return candidate.slug !== profile.slug &&
          profileState(candidate, selected) === "active";
      }
    );
    var protectedConditions = new Set(["always"]);
    otherActive.forEach(function (candidate) {
      candidate.conditions.forEach(function (condition) {
        protectedConditions.add(condition);
      });
    });
    profile.conditions.forEach(function (condition) {
      if (!protectedConditions.has(condition)) {
        selected.delete(condition);
      }
    });
    selected.add("always");
  }

  function loadSelection(data, notice) {
    var known = new Set(data.conditions.map(function (row) {
      return row.slug;
    }));
    var selected = new Set(["always"]);
    var unknown = [];
    var raw = new URL(window.location.href)
      .searchParams.get("c") || "";

    raw.split(",").filter(Boolean).forEach(function (slug) {
      if (slug === "always") {
        return;
      }
      if (known.has(slug)) {
        selected.add(slug);
      } else {
        unknown.push(slug);
      }
    });

    if (unknown.length) {
      notice.hidden = false;
      notice.textContent = unknown.map(function (slug) {
        return "'" + slug +
          "' is not a condition in this release; ignored.";
      }).join(" ");
    }
    return selected;
  }

  function orderedSelection(data, selected, includeFloor) {
    return data.conditions
      .map(function (row) {
        return row.slug;
      })
      .filter(function (slug) {
        return (includeFloor || slug !== "always") &&
          selected.has(slug);
      });
  }

  function replaceSelectionUrl(data, selected) {
    var ordered = orderedSelection(data, selected, false);
    var url = new URL(window.location.href);
    if (ordered.length) {
      url.searchParams.set("c", ordered.join(","));
    } else {
      url.searchParams.delete("c");
    }
    history.replaceState(
      null,
      "",
      url.pathname + url.search + url.hash
    );
  }

  function showView(name) {
    document.querySelectorAll("[data-view]").forEach(
      function (view) {
        view.hidden =
          view.getAttribute("data-view") !== name;
      }
    );
  }

  function renderAll(data, selected) {
    selected.add("always");

    var root = document.querySelector("[data-rules-app]");
    var ids = effectiveRuleIds(data, selected);
    var hits = new Map();

    data.rules.forEach(function (rule) {
      hits.set(
        rule["corpus-id"],
        rule.applies.filter(function (condition) {
          return selected.has(condition);
        }).length
      );
    });

    var onlySelected =
      root.querySelector("[data-only-selected]").checked;

    root.querySelectorAll("[data-rule-id]").forEach(
      function (row) {
        var id = row.getAttribute("data-rule-id");
        var included = ids.has(id);
        row.classList.toggle("is-out", !included);
        row.hidden = onlySelected && !included;

        var state = row.querySelector("[data-rule-state]");
        if (state) {
          state.textContent = included
            ? "in your set"
            : "not in your set";
        }

        var overlap = row.querySelector("[data-overlap]");
        if (overlap) {
          overlap.hidden =
            !included || hits.get(id) < 2;
        }
      }
    );

    root.querySelectorAll("[data-group]").forEach(
      function (section) {
        var total = new Set();
        var chosen = new Set();
        section.querySelectorAll("[data-rule-id]").forEach(
          function (row) {
            var id = row.getAttribute("data-rule-id");
            total.add(id);
            if (ids.has(id)) {
              chosen.add(id);
            }
          }
        );
        section.querySelector("[data-group-count]")
          .textContent =
          chosen.size + " of " + total.size;
      }
    );

    data.profiles.forEach(function (profile) {
      var button = root.querySelector(
        '[data-profile="' + profile.slug + '"]'
      );
      var state = profileState(profile, selected);
      button.setAttribute("data-state", state);
      button.setAttribute(
        "aria-pressed",
        state === "active" ? "true" : "false"
      );
      button.querySelector("[data-profile-state]")
        .textContent = state;
    });

    root.querySelectorAll("[data-condition]").forEach(
      function (control) {
        var slug = control.getAttribute("data-condition");
        control.checked = selected.has(slug);
        if (slug === "always") {
          control.checked = true;
          control.disabled = true;
        }
      }
    );

    var contributions = 0;
    data.rules.forEach(function (rule) {
      contributions += hits.get(rule["corpus-id"]);
    });
    var duplicateHits = contributions - ids.size;

    root.querySelector("[data-summary]").textContent =
      ids.size + " unique rules from " +
      contributions + " condition hits; the union removes " +
      duplicateHits + " duplicate hits.";

    var selectedNames =
      orderedSelection(data, selected, true);
    var selectedIds = data.rules
      .filter(function (rule) {
        return ids.has(rule["corpus-id"]);
      })
      .map(function (rule) {
        return rule["corpus-id"];
      });

    root.querySelector("[data-export-text]").textContent =
      "AIQT rule set\nConditions: " +
      selectedNames.join(", ") +
      "\nRule IDs (" + selectedIds.length + "):\n" +
      selectedIds.join("\n");

    replaceSelectionUrl(data, selected);
  }

  function bindControls(data, selected) {
    var root = document.querySelector("[data-rules-app]");

    root.querySelectorAll("[data-condition]").forEach(
      function (control) {
        control.addEventListener("change", function () {
          var slug =
            control.getAttribute("data-condition");
          if (slug === "always") {
            selected.add("always");
          } else if (control.checked) {
            selected.add(slug);
          } else {
            selected.delete(slug);
          }
          renderAll(data, selected);
        });
      }
    );

    data.profiles.forEach(function (profile) {
      root.querySelector(
        '[data-profile="' + profile.slug + '"]'
      ).addEventListener("click", function () {
        toggleProfile(profile, data, selected);
        renderAll(data, selected);
      });
    });

    root.querySelector("[data-only-selected]")
      .addEventListener("change", function () {
        renderAll(data, selected);
      });

    root.querySelectorAll('input[name="rules-view"]')
      .forEach(function (control) {
        control.addEventListener("change", function () {
          if (control.checked) {
            showView(control.value);
          }
        });
      });

    root.querySelector("[data-copy-link]")
      .addEventListener("click", function () {
        var button = this;
        var copied = function () {
          button.textContent = "Permalink copied";
          window.setTimeout(function () {
            button.textContent = "Copy permalink";
          }, 1800);
        };

        if (navigator.clipboard &&
            navigator.clipboard.writeText) {
          navigator.clipboard.writeText(
            window.location.href
          ).then(copied, function () {
            window.prompt(
              "Copy this permalink:",
              window.location.href
            );
          });
        } else {
          window.prompt(
            "Copy this permalink:",
            window.location.href
          );
        }
      });

    root.querySelector("[data-download-ids]")
      .addEventListener("click", function () {
        var blob = new Blob(
          [
            root.querySelector("[data-export-text]")
              .textContent + "\n"
          ],
          {type: "text/plain;charset=utf-8"}
        );
        var url = URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = "aiqt-rule-ids.txt";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
      });
  }

  var root = document.querySelector("[data-rules-app]");
  var status = document.querySelector(
    "[data-rules-load-status]"
  );
  if (!root || !status) {
    return;
  }

  fetch("/downloads/ruleset.json", {
    cache: "no-cache"
  }).then(function (response) {
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    return response.json();
  }).then(function (data) {
    validateRulesetShape(data);
    if (data["model-sha256"] !==
        root.getAttribute("data-model-sha256")) {
      throw new Error(
        "HTML and JSON model fingerprints differ"
      );
    }

    var notice = root.querySelector("[data-url-notice]");
    var selected = loadSelection(data, notice);
    bindControls(data, selected);
    renderAll(data, selected);

    root.querySelector("[data-builder]").hidden = false;
    root.querySelector("[data-view-switcher]").hidden = false;
    showView("facet");
    status.hidden = true;
    root.classList.add("is-enhanced");
  }).catch(function () {
    status.hidden = false;
    status.textContent =
      "The interactive builder could not load. " +
      "Everything below is the complete generated rule catalog.";
  });
}());
