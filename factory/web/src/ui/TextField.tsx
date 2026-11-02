import { useId, type InputHTMLAttributes } from "react";

// 1 行の入力。ラベルを必ず持ち、誤りの文を入力に結び付ける
export function TextField({ label, error, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string | null }) {
  const errorId = useId();
  return (
    <label className="ui-field">
      <span className="ui-field__label">{label}</span>
      <input className="ui-field__control" aria-invalid={error ? true : undefined} aria-describedby={error ? errorId : undefined} {...rest} />
      {error && (
        <span id={errorId} className="ui-field__error">
          {error}
        </span>
      )}
    </label>
  );
}
