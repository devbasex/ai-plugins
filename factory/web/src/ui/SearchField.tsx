import { useId } from "react";

// 一覧の絞り込みの入力。ラベルを必ず持つ
export function SearchField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = useId();
  return (
    <div className="ui-search">
      <label htmlFor={id} className="ui-field__label">
        {label}
      </label>
      <input id={id} type="search" className="ui-field__control" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
