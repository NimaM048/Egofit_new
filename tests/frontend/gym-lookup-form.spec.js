const { test, expect } = require("@playwright/test");
const path = require("path");

const projectRoot = path.resolve(__dirname, "../..");

test("repetition help follows the selected training method", async ({ page }) => {
  await page.setContent(`
    <form>
      <select
        id="method"
        data-repetition-help='{"normal":"بازهٔ صعودی مانند ۶-۱۲ وارد کنید.","drop_set":"۲ یا ۳ عدد با < یا > جدا کنید.","21_reps":"دقیقاً ۷+۷+۷ وارد کنید."}'
      >
        <option value="normal">Normal</option>
        <option value="drop_set">Drop Set</option>
        <option value="21_reps" selected>21 Reps</option>
      </select>
      <small data-repetition-help-text>راهنمای معمولی</small>
    </form>
  `);

  await page.addScriptTag({ path: path.join(projectRoot, "assets/js/gym-lookup-form.js") });

  const helpText = page.locator("[data-repetition-help-text]");
  await expect(helpText).toHaveText("دقیقاً ۷+۷+۷ وارد کنید.");

  await page.locator("#method").selectOption("drop_set");
  await expect(helpText).toHaveText("۲ یا ۳ عدد با < یا > جدا کنید.");
});
