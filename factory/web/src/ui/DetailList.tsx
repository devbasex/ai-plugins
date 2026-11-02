import type { ReactNode } from "react";

export interface DetailItem {
  term: string;
  value: ReactNode;
}

// 名前と値の組。狭い幅では縦に積む
export function DetailList({ items, label }: { items: DetailItem[]; label?: string }) {
  return (
    <dl className="ui-details" aria-label={label}>
      {items.map((item) => (
        <div key={item.term} className="ui-details__row">
          <dt>{item.term}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
