/* Progressive enhancement only. Django validates every submitted parameter. */
(() => {
  const form = document.querySelector("[data-session-form]");
  if (!form) return;
  const weapon = form.elements.weapon_type;
  const update = () => {
    for (const [selector, relevantWeapon] of [["[data-long-gun]", "laras_panjang"], ["[data-pistol]", "pistol"]]) {
      const group = form.querySelector(selector);
      const irrelevant = weapon.value !== "" && weapon.value !== relevantWeapon;
      // Keep conflicting values/errors visible after an invalid server response.
      const hasErrors = group.querySelector('[aria-invalid="true"]') !== null;
      group.hidden = irrelevant && !hasErrors;
      group.querySelector("select").disabled = irrelevant && !hasErrors;
    }
  };
  weapon.addEventListener("change", update);
  update();
})();
