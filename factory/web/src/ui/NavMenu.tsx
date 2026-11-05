import { useState } from "react";
import { NavLink, generatePath } from "react-router";
import { useTranslation } from "react-i18next";
import { ALL_ROLES, SCREENS, type Role } from "../screens.ts";

// 地図の nav の行き先。リンクは幅によらず常に描き、1024 px 未満では CSS で隠してメニューのボタンで開く
// accountId が無ければアカウントの要る行き先を、role が無ければ役割の要る行き先を出さない
export function NavMenu({ accountId, role }: { accountId: string | null; role: Role | null }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const items = SCREENS.filter((s) => s.nav && s.status === "available" && s.path !== null).flatMap((s) => {
    const path = s.path!;
    const needsAccount = path.includes(":accountId");
    if (needsAccount && accountId === null) return [];
    const everyone = ALL_ROLES.every((r) => s.roles.includes(r));
    if (!everyone && (role === null || !s.roles.includes(role))) return [];
    return [{ id: s.id, to: needsAccount ? generatePath(path, { accountId: accountId! }) : path }];
  });
  return (
    <nav aria-label={t("nav.label")} className="ui-nav">
      <button type="button" className="ui-nav__toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        {t("nav.menu")}
      </button>
      <div className={open ? "ui-nav__links ui-nav__links--open" : "ui-nav__links"}>
        {items.map((item) => (
          <NavLink key={item.id} to={item.to} end className="ui-nav__link" onClick={() => setOpen(false)}>
            {t(`screens.${item.id}`)}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
