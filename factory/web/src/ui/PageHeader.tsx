import type { ReactNode } from "react";

// 画面の題（h1）と、画面全体に掛かる操作
export function PageHeader({ title, actions }: { title: ReactNode; actions?: ReactNode }) {
  return (
    <header className="ui-page-header">
      <h1 className="ui-page-header__title">{title}</h1>
      {actions && <div className="ui-page-header__actions">{actions}</div>}
    </header>
  );
}
