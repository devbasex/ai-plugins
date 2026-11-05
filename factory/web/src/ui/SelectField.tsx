import type { SelectHTMLAttributes } from "react";

// 選択。ラベルを必ず持つ。選択肢は option を子に渡す
export function SelectField({ label, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <label className="ui-field">
      <span className="ui-field__label">{label}</span>
      <select className="ui-field__control" {...rest}>
        {children}
      </select>
    </label>
  );
}
