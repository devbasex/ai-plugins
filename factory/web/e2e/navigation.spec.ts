import { expect, test } from "@playwright/test";
import { PERSONAL } from "./paths.ts";

// 受け入れ条件 5: スマートフォンの幅で、ナビゲーションの行き先へメニュー → 行き先の 2 手で届く
const DESTINATIONS = [
  { from: `/accounts/${PERSONAL}/sprints`, name: "自分のアカウント", to: "/account" },
  { from: "/account", name: "スプリントの一覧", to: `/accounts/${PERSONAL}/sprints` },
  { from: "/account", name: "リポジトリ", to: `/accounts/${PERSONAL}/repositories` },
  { from: "/account", name: "キーの持ち込み", to: `/accounts/${PERSONAL}/model-credential` },
];

for (const d of DESTINATIONS) {
  test(`${d.from} から「${d.name}」へ 2 手で届く`, async ({ page }, info) => {
    test.skip(info.project.name !== "phone-360", "メニューのボタンは 1024 px 未満の幅で使う");
    await page.goto(d.from);
    const nav = page.getByRole("navigation", { name: "主な行き先" });
    const link = nav.getByRole("link", { name: d.name, exact: true });
    await expect(nav.getByRole("button", { name: "メニュー" })).toBeVisible();
    await expect(link).toBeHidden();
    await nav.getByRole("button", { name: "メニュー" }).click();
    await link.click();
    await expect(page).toHaveURL(d.to);
    await expect(link).toBeHidden();
  });
}

test("PC の幅では行き先が帯に並び、1 手で届く", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop-1280", "1024 px 以上の幅だけ");
  await page.goto("/account");
  const nav = page.getByRole("navigation", { name: "主な行き先" });
  await expect(nav.getByRole("button", { name: "メニュー" })).toBeHidden();
  await nav.getByRole("link", { name: "リポジトリ", exact: true }).click();
  await expect(page).toHaveURL(`/accounts/${PERSONAL}/repositories`);
});
