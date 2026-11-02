import type { ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "danger";

// ボタン。主・副・危険の 3 種。type の既定は button（フォームを意図せず送らない）
export function Button({ variant = "secondary", className, type = "button", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return <button type={type} className={["ui-button", `ui-button--${variant}`, className].filter(Boolean).join(" ")} {...rest} />;
}
