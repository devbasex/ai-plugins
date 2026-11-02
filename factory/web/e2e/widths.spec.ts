import { expect, test } from "@playwright/test";
import { SIGNED_IN_PATHS } from "./paths.ts";

// 受け入れ条件 9: どの幅でも横にはみ出さない
for (const path of SIGNED_IN_PATHS) {
  for (const search of ["", "?connection=offline"]) {
    test(`${path}${search} は横にはみ出さない`, async ({ page }) => {
      await page.goto(`${path}${search}`);
      await expect(page.locator("h1")).toBeVisible();
      await page.waitForLoadState("networkidle");
      // isMobile の幅では、はみ出すとブラウザが表示の幅を中身に合わせて広げ innerWidth も伸びるため、決めた端末の幅と比べる
      const width = page.viewportSize()!.width;
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(scrollWidth).toBeLessThanOrEqual(width);
    });
  }
}
